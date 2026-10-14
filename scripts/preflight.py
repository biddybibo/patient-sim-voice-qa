"""Phase 0 acceptance check: every credential works and the SIP trunk exists.
Costs a fraction of a cent (one tiny Claude request). Places no calls.

Run:  uv run python scripts/preflight.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import config  # noqa: E402  first: loads .env, sets SSL_CERT_FILE

import aiohttp  # noqa: E402
import anthropic  # noqa: E402
from livekit import api  # noqa: E402
from src.guard import check_dial_target  # noqa: E402

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def check_env() -> None:
    required = [
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SIP_OUTBOUND_TRUNK_ID",
        "CALLER_ID_E164", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "CARTESIA_API_KEY",
    ]
    missing = [k for k in required if not os.getenv(k)]
    record(".env complete", not missing, f"missing: {', '.join(missing)}" if missing else "")
    try:
        check_dial_target(os.getenv("TARGET_NUMBER", ""))
        record("TARGET_NUMBER is the test line", True)
    except ValueError as e:
        record("TARGET_NUMBER is the test line", False, str(e))


async def check_livekit() -> None:
    try:
        lkapi = api.LiveKitAPI()
    except ValueError as e:
        record("LiveKit credentials", False, str(e))
        return
    try:
        await lkapi.room.list_rooms(api.ListRoomsRequest())
        record("LiveKit credentials", True)
        trunks = await lkapi.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
        trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
        trunk = next((t for t in trunks.items if t.sip_trunk_id == trunk_id), None)
        if trunk is None:
            record("SIP outbound trunk", False, f"{trunk_id} not found (lk sip outbound list)")
        else:
            caller = os.getenv("CALLER_ID_E164")
            ok = caller in trunk.numbers
            record("SIP outbound trunk", ok, f"numbers={list(trunk.numbers)} caller={caller}")
    except Exception as e:  # report and keep checking the other providers
        record("LiveKit credentials", False, repr(e))
    finally:
        await lkapi.aclose()


async def check_http(name: str, url: str, headers: dict) -> None:
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url, headers=headers) as resp:
                record(name, resp.status == 200, f"HTTP {resp.status}")
    except aiohttp.ClientError as e:
        record(name, False, repr(e))


def check_claude() -> None:
    # Same request shape the live agent sends: thinking off, low effort.
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=config.LIVE_LLM_MODEL,
            max_tokens=32,
            messages=[{"role": "user", "content": "Say 'ok'."}],
            extra_body={"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}},
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        record(f"Claude ({config.LIVE_LLM_MODEL})", True, repr(text))
    except anthropic.APIStatusError as e:
        record(f"Claude ({config.LIVE_LLM_MODEL})", False, f"{e.status_code} {e.message}")
    except Exception as e:  # e.g. no API key configured
        record(f"Claude ({config.LIVE_LLM_MODEL})", False, repr(e))


async def main() -> None:
    check_env()
    await check_livekit()
    await check_http(
        "Deepgram key", "https://api.deepgram.com/v1/projects",
        {"Authorization": f"Token {os.getenv('DEEPGRAM_API_KEY', '')}"},
    )
    await check_http(
        "Cartesia key", "https://api.cartesia.ai/voices?limit=1",
        {"X-API-Key": os.getenv("CARTESIA_API_KEY", ""), "Cartesia-Version": "2025-04-16"},
    )
    check_claude()

    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
