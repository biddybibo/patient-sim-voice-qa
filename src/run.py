"""One command to run one call: start the agent worker, wait for it to register,
place the call, wait for it to finish, then stop the worker.

Run:  uv run python -m src.run --scenario hello-world
"""

import argparse
import asyncio
import collections
import os
import subprocess
import sys
import threading
import time

from src.config import CALLS_DIR
from src.dial import place_call, wait_for_call_end
from src.persona import list_scenarios, load_plan

WORKER_READY_LINE = "registered worker"
WORKER_START_TIMEOUT = 60
SAVE_TIMEOUT = 60
# Watchdog margin past the scenario's time limit (the bot's own wrap-up takes up to ~30 s).
WATCHDOG_GRACE = 90


def start_worker() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.agent", "start"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    ready = threading.Event()
    recent = collections.deque(maxlen=5)

    def pump() -> None:
        # Echo worker logs and watch for the registration line.
        for line in proc.stdout:
            sys.stdout.write("  [worker] " + line)
            recent.append(line.strip())
            if WORKER_READY_LINE in line:
                ready.set()

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.time() + WORKER_START_TIMEOUT
    while not ready.wait(0.5):
        if proc.poll() is not None or time.time() > deadline:
            proc.terminate()
            last = "\n  ".join(recent) or "(no output)"
            raise SystemExit(f"Worker failed to register with LiveKit. Last worker output:\n  {last}")
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
    parser.add_argument("--scenario", default="simple-scheduling", help=", ".join(list_scenarios()))
    parser.add_argument("--persona", help="override the scenario's persona (see scenarios/personas.yaml)")
    parser.add_argument("--llm", help="live LLM model id, e.g. claude-sonnet-5-5 (default: config.LIVE_LLM_MODEL)")
    parser.add_argument("--max-minutes", type=float, help="override the scenario's time limit (testing)")
    parser.add_argument(
        "--no-worker", action="store_true", help="use a worker you already started"
    )
    args = parser.parse_args()

    if args.llm:
        os.environ["LIVE_LLM_MODEL"] = args.llm  # inherited by the worker subprocess
    plan = load_plan(args.scenario, args.persona)  # fail fast before starting anything
    if args.max_minutes:
        plan.max_seconds = int(args.max_minutes * 60)
    print(f"Scenario {args.scenario} as {plan.facts['name']} ({plan.persona_id}), limit {plan.max_seconds}s")

    worker = None if args.no_worker else start_worker()
    try:
        call = asyncio.run(place_call(args.scenario, args.persona, args.max_minutes))
        asyncio.run(wait_for_call_end(call.room_name, plan.max_seconds + WATCHDOG_GRACE))
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
            try:
                worker.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker.kill()  # a stuck worker must not block the next call
                worker.wait()


if __name__ == "__main__":
    main()
