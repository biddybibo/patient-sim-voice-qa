"""LiveKit worker: the patient caller.

Pipeline mode: Deepgram STT -> Claude LLM -> Cartesia TTS, with Silero VAD and
LiveKit's semantic turn detector. The bot joins the room created by dial.py,
waits for the clinic's phone line to join, and stays silent until the clinic
agent speaks first.

Run:  uv run python -m src.agent dev       (worker, connects to LiveKit Cloud)
      uv run python -m src.agent console   (talk to the bot locally, no phone call)
"""

import asyncio
import json
import logging
import os

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
    room_io,
)
from livekit.agents.utils import is_given
from livekit.plugins import anthropic, cartesia, deepgram, noise_cancellation, silero


logger = logging.getLogger("patient-caller")

# If the clinic says nothing this long after answering, nudge with "Hello?".
GREETING_WAIT_SECONDS = 6.0

# Placeholder persona for Phase 1. Phase 3 replaces this with scenarios/*.yaml + personas.yaml.
PATIENT_NAME = os.getenv("PATIENT_NAME", "Jordan Miller")
PATIENT_DOB = os.getenv("PATIENT_DOB", "March 4th, 1988")
PATIENT_PHONE = os.getenv("PATIENT_PHONE", "555-201-4477")

PHASE1_INSTRUCTIONS = f"""\
You are {PATIENT_NAME}, a patient calling your doctor's office on the phone. Your date of birth is {PATIENT_DOB} and your phone number is {PATIENT_PHONE}.
You want to book a routine checkup sometime next week, ideally a morning.

How to talk:
- This is a phone call. Keep each turn to one or two short sentences, casual and natural, with contractions.
- Never use lists, markdown, or emojis. Say dates and numbers the way people say them out loud.
- Answer verification questions with your details. Spell your name only if asked.
- Never say you are an AI or a test.
- Once the appointment is booked and confirmed back to you, say a short thank-you and goodbye, then call end_call.
- If they can't help after two tries, politely wrap up and call end_call.
"""


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
    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions)

    @function_tool
    async def end_call(self, context: RunContext, reason: str) -> None:
        """Hang up the phone. Only call this after you have already said goodbye.

        Args:
            reason: Why the call is ending: goal_met, blocked, transferred, or loop_detected.
        """
        logger.info("end_call requested: %s", reason)
        await context.wait_for_playout()
        await get_job_context().delete_room()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name=config.AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    metadata = json.loads(ctx.job.metadata or "{}")
    logger.info("job started in room %s, metadata=%s", ctx.room.name, metadata)

    session = AgentSession(
        stt=deepgram.STT(model=config.STT_MODEL, keyterm=[PATIENT_NAME]),
        llm=FastClaude(model=config.LIVE_LLM_MODEL, caching="ephemeral"),
        tts=cartesia.TTS(model=config.TTS_MODEL),
        vad=ctx.proc.userdata["vad"],
        turn_handling={
            "turn_detection": inference.TurnDetector(),
            "endpointing": {"min_delay": 0.6, "max_delay": 3.0},
            # Two bots talking: let the bot finish its (short) turn rather than
            # getting into overlap/echo loops. Barge-in scenarios turn this on later.
            "interruption": {"enabled": False},
            "preemptive_generation": {"enabled": True},
        },
        # The clinic agent may go quiet while it looks something up; don't treat that as "away".
        user_away_timeout=None,
    )

    heard_clinic = asyncio.Event()

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        if ev.new_state == "speaking":
            heard_clinic.set()

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

    await session.start(
        agent=PatientAgent(PHASE1_INSTRUCTIONS),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
            participant_identity=participant_identity,
            participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_SIP],
            delete_room_on_close=True,
        ),
    )

    # The callee speaks first. Only if the line stays silent do we say hello.
    try:
        await asyncio.wait_for(heard_clinic.wait(), timeout=GREETING_WAIT_SECONDS)
    except asyncio.TimeoutError:
        logger.info("no greeting after %.0fs, saying hello", GREETING_WAIT_SECONDS)
        session.say("Hello?")


if __name__ == "__main__":
    cli.run_app(server)
