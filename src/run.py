"""One command to run one call: start the agent worker, wait for it to register,
place the call, wait for it to finish, then stop the worker.

Run:  uv run python -m src.run --scenario hello-world
"""

import argparse
import asyncio
import subprocess
import sys
import threading
import time

from src.config import CALLS_DIR
from src.dial import place_call, wait_for_call_end

WORKER_READY_LINE = "registered worker"
WORKER_START_TIMEOUT = 60
SAVE_TIMEOUT = 60


def start_worker() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.agent", "start"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    ready = threading.Event()

    def pump() -> None:
        # Echo worker logs and watch for the registration line.
        for line in proc.stdout:
            sys.stdout.write("  [worker] " + line)
            if WORKER_READY_LINE in line:
                ready.set()

    threading.Thread(target=pump, daemon=True).start()
    if not ready.wait(WORKER_START_TIMEOUT):
        proc.terminate()
        raise SystemExit("Worker did not register with LiveKit in time; check .env credentials.")
    return proc


def wait_for_file(path, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="hello-world")
    parser.add_argument(
        "--no-worker", action="store_true", help="use a worker you already started"
    )
    args = parser.parse_args()

    worker = None if args.no_worker else start_worker()
    try:
        call = asyncio.run(place_call(args.scenario))
        asyncio.run(wait_for_call_end(call.room_name))
        print(f"Call ended after {time.time() - call.answered_at:.0f}s")
        call_dir = CALLS_DIR / call.call_id
        if wait_for_file(call_dir / "metrics.json", SAVE_TIMEOUT):
            print(f"\nSaved {call_dir}:")
            print((call_dir / "transcript.txt").read_text())
        else:
            print(f"Worker did not write {call_dir} within {SAVE_TIMEOUT}s; check worker logs.")
    finally:
        if worker:
            worker.terminate()
            worker.wait(timeout=15)


if __name__ == "__main__":
    main()
