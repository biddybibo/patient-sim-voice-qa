"""Place one outbound call: create a room, dispatch the patient agent into it,
then dial the clinic line into the same room over SIP.

Run:  uv run python -m src.dial --scenario hello-world
(expects a worker already running; src.run starts one for you)
"""

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass

from src import config  # first: sets SSL_CERT_FILE before aiohttp loads

from livekit import api
from src.guard import check_dial_target


@dataclass
class CallInfo:
    room_name: str
    dialed_at: float
    answered_at: float


async def place_call(scenario: str) -> CallInfo:
    target = check_dial_target(os.getenv("TARGET_NUMBER", "+18054398008"))
    trunk_id = config.require("SIP_OUTBOUND_TRUNK_ID")
    room_name = f"call-{scenario}-{int(time.time())}"

    lkapi = api.LiveKitAPI()
    try:
        # Room first (Phase 2 attaches auto-egress here), then the agent, then the phone.
        await lkapi.room.create_room(
            api.CreateRoomRequest(name=room_name, empty_timeout=30, departure_timeout=10)
        )
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=config.AGENT_NAME,
                room=room_name,
                metadata=json.dumps({"scenario": scenario}),
            )
        )

        dialed_at = time.time()
        print(f"Dialing {target} into room {room_name} ...")
        await lkapi.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=target,
                room_name=room_name,
                participant_identity="clinic-line",
                participant_name="Clinic",
                wait_until_answered=True,
                max_call_duration=api.Duration(seconds=config.MAX_CALL_SECONDS),
            )
        )
        answered_at = time.time()
        print(f"Answered after {answered_at - dialed_at:.1f}s")
        return CallInfo(room_name, dialed_at, answered_at)
    except api.TwirpError as e:
        sip_status = e.metadata.get("sip_status_code"), e.metadata.get("sip_status")
        raise SystemExit(f"Call failed: {e.message} (SIP {sip_status})") from e
    finally:
        await lkapi.aclose()


async def wait_for_call_end(room_name: str, poll_seconds: float = 2.0) -> None:
    """Block until the room is gone (bot hung up, clinic hung up, or timeout)."""
    lkapi = api.LiveKitAPI()
    try:
        while True:
            rooms = await lkapi.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
            if not rooms.rooms:
                return
            await asyncio.sleep(poll_seconds)
    finally:
        await lkapi.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="hello-world")
    args = parser.parse_args()

    async def run() -> None:
        call = await place_call(args.scenario)
        await wait_for_call_end(call.room_name)
        print(f"Call ended after {time.time() - call.answered_at:.0f}s")

    asyncio.run(run())


if __name__ == "__main__":
    main()
