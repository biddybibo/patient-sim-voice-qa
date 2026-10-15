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
    StopResponse,
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
from src.audio_analysis import analyze
from src.persona import CallPlan, load_plan
from src.recording import save_audio

logger = logging.getLogger("patient-caller")

# If the clinic says nothing this long after answering, nudge with "Hello?".
GREETING_WAIT_SECONDS = 6.0
# Silence that ends a clinic turn. The greeting gets a longer wait: a recorded disclaimer
# followed by a ~0.6 s pause and then the greeting otherwise reads as a finished turn
# (call 002 at 00:08.2). Back to the normal delay once the bot first speaks.
GREETING_ENDPOINTING_DELAY = 1.5
ENDPOINTING_DELAY = 0.6
# Mid-call silence (both sides quiet) before checking the line is still there.
SILENCE_CHECK_SECONDS = 15.0
# end_call(goal_met) earlier than this is refused: real calls with verification take longer,
# and an early hang-up usually means the bot skipped confirming the details.
MIN_CALL_SECONDS = 45
# Longest we wait for our own sentence, then the fixed goodbye, to finish playing at the time limit.
WRAP_UP_PLAYOUT_SECONDS = 15
# At the time limit, if we just asked something, how long to wait for the clinic's answer before leaving.
WRAP_UP_ANSWER_SECONDS = 25
# After "transferring you", how long to wait for someone to speak before hanging up.
TRANSFER_WAIT_SECONDS = 30
# Clinic turns this similar (0-1) count as the agent repeating itself.
REPEAT_SIMILARITY = 0.8

DTMF_CODES = {**{str(d): d for d in range(10)}, "*": 10, "#": 11}

# A clinic turn that's only "let me check / one moment / please hold" gets no reply: a filler
# like "Sure, take your time" lands on top of their answer (calls 003 at 00:58.5, 006 at 00:52.2).
HOLD_PHRASES = (
    "let me check", "let me look", "let me pull", "let me see", "let me get", "one moment",
    "just a moment", "one second", "please hold", "bear with me", "while i check", "i'm checking",
    "i am checking", "i'm processing", "i am processing",
)


def is_hold_only(text: str) -> bool:
    text = text.lower().strip()
    return "?" not in text and len(text.split()) <= 25 and any(p in text for p in HOLD_PHRASES)


class FastClaude(anthropic.LLM):
    """Claude with thinking off and low effort: a patient's reply doesn't need reasoning,
    and every millisecond of time-to-first-token is dead air on the call."""

    def chat(self, *, extra_kwargs=NOT_GIVEN, **kwargs):
        extra = dict(extra_kwargs) if is_given(extra_kwargs) else {}
        body = dict(extra.get("extra_body") or {})
        if self.model in config.THINKING_OFF:
            body.setdefault("thinking", config.THINKING_OFF[self.model])
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
        if self.session.userdata.get("ending"):
            # We've said goodbye; anything the clinic says now gets no reply (call 006 at 03:07.7
            # the bot started a new question after its goodbye).
            raise StopResponse()
        if answered := self.session.userdata.get("wrap_up_answered"):
            # Time limit reached while we were waiting for this answer: the goodbye replies to it.
            answered.set()
            raise StopResponse()
        text = (new_message.text_content or "").lower()
        if is_hold_only(text):
            logger.info("clinic is checking something; staying quiet")
            raise StopResponse()
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
        # From here on, the clinic's "goodbye" must not cancel this speech (and with it the hang-up).
        context.userdata["ending"] = True
        await context.wait_for_playout()
        await get_job_context().delete_room()
        return None

    @function_tool
    async def wait_on_transfer(self, context: RunContext) -> str:
        """Call this when they say they're transferring you or connecting you to someone.
        You stay on the line; don't say goodbye."""
        userdata = context.userdata
        started = time.time()
        logger.info("transfer started; waiting up to %ds for someone to pick up", TRANSFER_WAIT_SECONDS)

        async def hang_up_if_nobody_answers() -> None:
            await asyncio.sleep(TRANSFER_WAIT_SECONDS)
            if userdata.get("last_clinic_speech", 0) < started:
                logger.info("nobody picked up after transfer; hanging up")
                userdata["end_reason"] = userdata.get("end_reason") or "transferred_no_answer"
                userdata["ending"] = True
                await get_job_context().delete_room()

        asyncio.create_task(hang_up_if_nobody_answers())
        return (
            "You're on hold for the transfer. Stay silent until someone speaks, then explain what you need. "
            "If it's a recording or they say goodbye, just say \"okay, bye\" and call end_call."
        )

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
# port=0: pick any free port for the worker's health server, so a worker that's still shutting
# down from the previous call can't block the next one (address already in use on 8081).
server = AgentServer(setup_fnc=prewarm, num_idle_processes=1, port=0)


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

    # Audio work is CPU-bound (~1 s for a 3-minute call): keep it off the event loop.
    if report.audio_recording_path and report.audio_recording_path.exists():
        await asyncio.to_thread(save_audio, report.audio_recording_path, call_dir)
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
            "llm": session.userdata.get("llm"),
            "room": report.room,
            "recording_started_at": t0,
        },
    )
    end_reason = session.userdata.get("end_reason") or "remote_hangup"
    recording = call_dir / "recording.ogg"
    audio = await asyncio.to_thread(analyze, recording) if recording.exists() else None
    call_metrics = metrics.compute(turns, report.duration, end_reason, audio)
    (call_dir / "metrics.json").write_text(json.dumps(call_metrics, indent=2))
    logger.info("saved call to %s", call_dir)


@server.rtc_session(agent_name=config.AGENT_NAME, on_session_end=save_call)
async def entrypoint(ctx: JobContext) -> None:
    metadata = json.loads(ctx.job.metadata or "{}")
    scenario_id = metadata.get("scenario") or os.getenv("SCENARIO", "simple-scheduling")
    plan = load_plan(scenario_id, persona_override=metadata.get("persona"))
    if metadata.get("max_minutes"):  # test override (src.run --max-minutes)
        plan.max_seconds = int(float(metadata["max_minutes"]) * 60)
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
            "endpointing": {"min_delay": GREETING_ENDPOINTING_DELAY, "max_delay": 3.0},
            # Two bots talking: let the bot finish its (short) turn rather than getting into
            # overlap/echo loops. Barge-in scenarios turn this on via flags.allow_interruptions.
            "interruption": {"enabled": plan.allow_interruptions},
            "preemptive_generation": {"enabled": True},
        },
        # Both sides quiet this long -> user_state "away" -> we check the line (see below).
        user_away_timeout=SILENCE_CHECK_SECONDS,
        userdata={
            "end_reason": None,
            "persona": plan.persona_id,
            "llm": config.LIVE_LLM_MODEL,
            "started_at": time.time(),
        },
    )

    heard_clinic = asyncio.Event()

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        if ev.new_state == "speaking":
            heard_clinic.set()
            session.userdata["last_clinic_speech"] = time.time()
            # The clinic kept talking after we decided their turn was over: drop the queued
            # reply if none of it has played yet (call 002 at 00:08.8). Interruptions are
            # off, so LiveKit won't do this for us.
            speech = session.current_speech
            if (
                speech
                and not speech.done()
                and session.agent_state != "speaking"
                and not session.userdata.get("ending")
            ):
                logger.info("clinic resumed before our reply played; dropping it")
                speech.interrupt(force=True)
        elif ev.new_state == "away" and heard_clinic.is_set() and not session.userdata.get("ending"):
            # Long silence mid-call (e.g. a lookup that never came back): check once per silence.
            logger.info("line silent for %.0fs, checking in", SILENCE_CHECK_SECONDS)
            session.generate_reply(
                instructions=(
                    "The line has been silent for a while. If you've already told them that's everything, "
                    "say a short warm goodbye and call end_call. Otherwise briefly ask if they're still there."
                )
            )

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        if ev.new_state == "speaking" and session.options.endpointing["min_delay"] != ENDPOINTING_DELAY:
            session.update_options(endpointing_opts={"min_delay": ENDPOINTING_DELAY})

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

    @ctx.room.on("participant_disconnected")
    def _on_participant_left(participant: rtc.RemoteParticipant) -> None:
        # LiveKit only closes the session for some disconnect reasons; for others (call 025) the bot
        # sat alone in the room for 84 minutes after the phone line was gone. Any reason ends the call.
        if participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return
        reason = rtc.DisconnectReason.Name(participant.disconnect_reason or 0)
        logger.info("phone line left (%s); ending the call", reason)
        session.userdata["end_reason"] = session.userdata.get("end_reason") or "remote_hangup"
        session.userdata["ending"] = True
        ctx.delete_room()  # schedules its own task (wrapping it in create_task raised TypeError)

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
        """At the scenario's time limit, say a fixed goodbye and hang up.

        Not an LLM instruction: asked to wrap up, the model sometimes kept asking questions until it was
        cut off (calls 020, 023), and a fixed grace timer hung up mid-goodbye (call 012)."""
        await asyncio.sleep(plan.max_seconds)
        if session.userdata.get("ending"):
            return
        logger.info("scenario time limit (%ds) reached, wrapping up", plan.max_seconds)
        session.userdata["end_reason"] = session.userdata["end_reason"] or "timeout"
        try:
            # If we spoke last, we probably asked something: let the clinic answer first, so the goodbye
            # doesn't land right after our own question (calls 004, 018, 029).
            last = next((i for i in reversed(session.history.items) if i.type == "message"), None)
            if last is not None and last.role == "assistant":
                answered = asyncio.Event()
                session.userdata["wrap_up_answered"] = answered
                try:
                    await asyncio.wait_for(answered.wait(), WRAP_UP_ANSWER_SECONDS)
                except asyncio.TimeoutError:
                    pass
            session.userdata["ending"] = True  # no more replies to the clinic from here on
            # Don't cut the clinic off mid-sentence (call 006 at 03:01 did): wait briefly for a gap.
            for _ in range(20):
                if session.user_state != "speaking":
                    break
                await asyncio.sleep(0.5)
            # Let a sentence that's already playing finish; drop a reply that hasn't started.
            current = session.current_speech
            if current and not current.done():
                if session.agent_state == "speaking":
                    await asyncio.wait_for(current.wait_for_playout(), WRAP_UP_PLAYOUT_SECONDS)
                else:
                    current.interrupt(force=True)
            # A goodbye that fits the moment (a fixed line "thanked" answers that never came and promised to
            # call back after a booking was done: calls 040-044). Bounded: one sentence, and we hang up after it
            # plays whatever it says. The persona's fixed line is the fallback.
            try:
                goodbye = session.generate_reply(
                    instructions=(
                        "You're out of time and must hang up now. Say ONE short, warm sentence that fits where the "
                        "call is: if what you called about got done, thank them for it; if not, say you'll call back. "
                        "Then say bye. No questions."
                    ),
                    # Tools stay available (tool_choice="none" sends an empty tool list, which the API rejects
                    # when earlier turns contain tool calls); we hang up after playout either way.
                    allow_interruptions=False,
                )
                await asyncio.wait_for(goodbye.wait_for_playout(), WRAP_UP_PLAYOUT_SECONDS)
            except Exception:
                logger.exception("contextual goodbye failed; using the persona's fixed line")
                goodbye = session.say(plan.goodbye, allow_interruptions=False)
                await asyncio.wait_for(goodbye.wait_for_playout(), WRAP_UP_PLAYOUT_SECONDS)
        except Exception:
            logger.exception("wrap-up failed; hanging up anyway")
        finally:
            # Always hang up, whatever happened above.
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
