"""LiveKit worker: the patient caller.

Pipeline mode: Deepgram STT -> Claude LLM -> Cartesia TTS, with Silero VAD and
LiveKit's semantic turn detector. The bot joins the room created by dial.py,
waits for the clinic's phone line to join, stays silent until the clinic agent
speaks first, then plays the scenario's patient (see src/persona.py).

Run:  uv run python -m src.agent dev       (worker, connects to LiveKit Cloud)
      SCENARIO=reschedule uv run python -m src.agent console   (talk to the bot locally)
"""

import asyncio
import difflib
import json
import logging
import os
import time

from src import config  # first: sets SSL_CERT_FILE before aiohttp loads

from livekit import rtc
from livekit.agents import (
    NOT_GIVEN,
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    get_job_context,
    inference,
    llm,
    room_io,
)
from livekit.agents.utils import is_given
from livekit.plugins import anthropic, cartesia, deepgram, noise_cancellation, silero

from src import metrics, transcript
from src.persona import CallPlan, load_plan
from src.recording import save_audio

logger = logging.getLogger("patient-caller")

# If the clinic says nothing this long after answering, nudge with "Hello?".
GREETING_WAIT_SECONDS = 6.0
# Mid-call silence (both sides quiet) before checking the line is still there.
SILENCE_CHECK_SECONDS = 15.0
# end_call(goal_met) earlier than this is refused: real calls with verification take longer,
# and an early hang-up usually means the bot skipped confirming the details.
MIN_CALL_SECONDS = 45
# Time allowed for the wrap-up goodbye after the scenario's time limit before forcing hang-up.
WRAP_UP_GRACE_SECONDS = 20
# Clinic turns this similar (0-1) count as the agent repeating itself.
REPEAT_SIMILARITY = 0.8

DTMF_CODES = {**{str(d): d for d in range(10)}, "*": 10, "#": 11}


class FastClaude(anthropic.LLM):
    """Claude with thinking off and low effort: a patient's reply doesn't need reasoning,
    and every millisecond of time-to-first-token is dead air on the call."""

    def chat(self, *, extra_kwargs=NOT_GIVEN, **kwargs):
        extra = dict(extra_kwargs) if is_given(extra_kwargs) else {}
        body = dict(extra.get("extra_body") or {})
        body.setdefault("thinking", {"type": "disabled"})
        body.setdefault("output_config", {"effort": "low"})
        extra["extra_body"] = body
        return super().chat(extra_kwargs=extra, **kwargs)


class PatientAgent(Agent):
    def __init__(self, plan: CallPlan) -> None:
        super().__init__(instructions=plan.instructions)
        self._clinic_turns: list[str] = []
        self._loop_warned = False

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Loop guard: if the clinic agent asks essentially the same thing a third time,
        tell the caller to rephrase once more and then give up (a bug worth reporting)."""
        text = (new_message.text_content or "").lower()
        if len(text) < 25 or self._loop_warned:
            self._clinic_turns.append(text)
            return
        repeats = sum(
            difflib.SequenceMatcher(None, text, prev).ratio() >= REPEAT_SIMILARITY
            for prev in self._clinic_turns[-6:]
        )
        self._clinic_turns.append(text)
        if repeats >= 2:
            self._loop_warned = True
            logger.info("loop guard: clinic repeated itself %d times", repeats + 1)
            turn_ctx.add_message(
                role="system",
                content=(
                    "They have now asked you essentially the same thing three times. "
                    "Rephrase your answer once more, clearly. If they repeat it again after that, "
                    "say you'll call back later, say goodbye, and call end_call with reason loop_detected."
                ),
            )

    @function_tool
    async def end_call(self, context: RunContext, reason: str) -> str | None:
        """Hang up the phone. Only call this after you have already said goodbye.

        Args:
            reason: Why the call is ending: goal_met, blocked, transferred, loop_detected, or timeout.
        """
        elapsed = time.time() - context.userdata["started_at"]
        if reason == "goal_met" and elapsed < MIN_CALL_SECONDS:
            logger.info("end_call refused after %.0fs", elapsed)
            return (
                "Don't hang up yet. Before ending, ask them to confirm the key details "
                "back to you (date, time, who it's with, or what happens next)."
            )
        logger.info("end_call requested after %.0fs: %s", elapsed, reason)
        context.userdata["end_reason"] = context.userdata.get("end_reason") or reason
        await context.wait_for_playout()
        await get_job_context().delete_room()
        return None

    @function_tool
    async def send_dtmf(self, context: RunContext, digits: str) -> str:
        """Press keys on the phone keypad, for automated phone menus ("press 1 for...").

        Args:
            digits: The keys to press, e.g. "1" or "2#". Allowed: 0-9, * and #.
        """
        room = get_job_context().room
        for digit in digits:
            if digit in DTMF_CODES:
                await room.local_participant.publish_dtmf(code=DTMF_CODES[digit], digit=digit)
                await asyncio.sleep(0.3)
        logger.info("sent DTMF %s", digits)
        return f"Pressed {digits}."


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


# One call at a time, so one warm process is enough (production default is 10).
server = AgentServer(setup_fnc=prewarm, num_idle_processes=1)


async def save_call(ctx: JobContext) -> None:
    """Runs after the session has closed (recording finalized), before temp files are deleted."""
    meta = json.loads(ctx.job.metadata or "{}")
    call_id = meta.get("call_id")
    if not call_id:  # console mode
        return
    try:
        session = ctx.primary_session
    except RuntimeError:  # the phone line never joined, so no session was started
        logger.info("no session for %s; nothing to save", call_id)
        return

    report = ctx.make_session_report(session)
    call_dir = config.CALLS_DIR / call_id
    t0 = report.audio_recording_started_at or report.started_at

    if report.audio_recording_path and report.audio_recording_path.exists():
        save_audio(report.audio_recording_path, call_dir)
    else:
        logger.warning("no audio recording found for %s", call_id)

    turns = transcript.build_turns(report.chat_history.items, t0)
    transcript.write_transcript(
        turns,
        call_dir,
        header={
            "call_id": call_id,
            "scenario": meta.get("scenario"),
            "persona": session.userdata.get("persona"),
            "room": report.room,
            "recording_started_at": t0,
        },
    )
    end_reason = session.userdata.get("end_reason") or "remote_hangup"
    call_metrics = metrics.compute(turns, report.duration, end_reason)
    (call_dir / "metrics.json").write_text(json.dumps(call_metrics, indent=2))
    logger.info("saved call to %s", call_dir)


@server.rtc_session(agent_name=config.AGENT_NAME, on_session_end=save_call)
async def entrypoint(ctx: JobContext) -> None:
    metadata = json.loads(ctx.job.metadata or "{}")
    scenario_id = metadata.get("scenario") or os.getenv("SCENARIO", "simple-scheduling")
    plan = load_plan(scenario_id, persona_override=metadata.get("persona"))
    logger.info("job in room %s: scenario=%s persona=%s", ctx.room.name, scenario_id, plan.persona_id)

    session = AgentSession(
        stt=deepgram.STT(model=config.STT_MODEL, keyterm=[k for k in plan.keyterms if k]),
        llm=FastClaude(model=config.LIVE_LLM_MODEL, caching="ephemeral"),
        tts=cartesia.TTS(
            model=config.TTS_MODEL, voice=plan.voice, speed=plan.speed, emotion=plan.emotion
        ),
        vad=ctx.proc.userdata["vad"],
        turn_handling={
            "turn_detection": inference.TurnDetector(),
            "endpointing": {"min_delay": 0.6, "max_delay": 3.0},
            # Two bots talking: let the bot finish its (short) turn rather than getting into
            # overlap/echo loops. Barge-in scenarios turn this on via flags.allow_interruptions.
            "interruption": {"enabled": plan.allow_interruptions},
            "preemptive_generation": {"enabled": True},
        },
        # Both sides quiet this long -> user_state "away" -> we check the line (see below).
        user_away_timeout=SILENCE_CHECK_SECONDS,
        userdata={"end_reason": None, "persona": plan.persona_id, "started_at": time.time()},
    )

    heard_clinic = asyncio.Event()

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        if ev.new_state == "speaking":
            heard_clinic.set()
        elif ev.new_state == "away" and heard_clinic.is_set():
            # Long silence mid-call (e.g. a lookup that never came back): check once per silence.
            logger.info("line silent for %.0fs, checking in", SILENCE_CHECK_SECONDS)
            session.generate_reply(
                instructions="The line has been silent for a while. Briefly ask if they're still there."
            )

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if getattr(item, "type", "message") != "message":
            return
        speaker = "PATIENT_BOT" if item.role == "assistant" else "CLINIC_AGENT"
        logger.info("%s: %s", speaker, item.text_content)

    await ctx.connect()

    # In a real call the phone line joins as a SIP participant. In console mode there is
    # no room to wait on, so we start straight away.
    participant_identity = NOT_GIVEN
    if not ctx.is_fake_job():
        participant = await ctx.wait_for_participant(
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        )
        participant_identity = participant.identity
        logger.info("phone line joined: %s", participant_identity)

    session.userdata["started_at"] = time.time()
    await session.start(
        agent=PatientAgent(plan),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
            participant_identity=participant_identity,
            participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_SIP],
            delete_room_on_close=True,
        ),
        # Built-in recorder: stereo OGG, clinic and bot on separate channels, one timeline.
        record={"audio": True, "traces": False, "logs": False},
    )

    async def enforce_time_limit() -> None:
        await asyncio.sleep(plan.max_seconds)
        logger.info("scenario time limit (%ds) reached, wrapping up", plan.max_seconds)
        session.userdata["end_reason"] = session.userdata["end_reason"] or "timeout"
        session.generate_reply(
            instructions="You're out of time for this call. Politely say you have to go, say goodbye, then call end_call with reason timeout."
        )
        await asyncio.sleep(WRAP_UP_GRACE_SECONDS)
        await ctx.delete_room()

    time_limit = asyncio.create_task(enforce_time_limit())

    async def cancel_time_limit() -> None:
        time_limit.cancel()

    ctx.add_shutdown_callback(cancel_time_limit)

    # The callee speaks first. Only if the line stays silent do we say hello.
    try:
        await asyncio.wait_for(heard_clinic.wait(), timeout=GREETING_WAIT_SECONDS)
    except asyncio.TimeoutError:
        logger.info("no greeting after %.0fs, saying hello", GREETING_WAIT_SECONDS)
        session.say("Hello?")


if __name__ == "__main__":
    cli.run_app(server)
