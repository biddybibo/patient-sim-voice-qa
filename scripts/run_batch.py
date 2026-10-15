"""Run several scenarios back to back, one call at a time, with a cooldown between calls.

Each item is `scenario` or `scenario:persona` (persona override, for reruns that should sound
different). A call that fails to connect (SIP error, worker didn't start) is retried once.

Run:  uv run python scripts/run_batch.py chest-pain wrong-dob privacy-shared-phone:skeptical
      uv run python scripts/run_batch.py --all
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import CALLS_DIR  # noqa: E402
from src.persona import list_scenarios, load_plan  # noqa: E402


def call_folders() -> set[str]:
    return {p.name for p in CALLS_DIR.iterdir() if p.is_dir() and p.name[:3].isdigit()}


def run_one(scenario: str, persona: str | None) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "src.run", "--scenario", scenario]
    if persona:
        cmd += ["--persona", persona]
    before = call_folders()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    new = sorted(call_folders() - before)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    return proc.returncode == 0 and bool(new), (new[0] if new else tail[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("items", nargs="*", help="scenario or scenario:persona")
    parser.add_argument("--all", action="store_true", help="every scenario in scenarios/")
    parser.add_argument("--cooldown", type=int, default=75, help="seconds between calls")
    args = parser.parse_args()

    items = list_scenarios() if args.all else args.items
    plan = [(i.split(":")[0], i.split(":")[1] if ":" in i else None) for i in items]
    for scenario, persona in plan:
        load_plan(scenario, persona)  # fail fast on a typo before placing any call

    results = []
    for n, (scenario, persona) in enumerate(plan):
        if n:
            time.sleep(args.cooldown)
        label = f"{scenario}" + (f" ({persona})" if persona else "")
        print(f"[{time.strftime('%H:%M:%S')}] {n + 1}/{len(plan)} {label}", flush=True)
        ok, detail = run_one(scenario, persona)
        if not ok:
            print(f"    failed: {detail}; retrying once after cooldown", flush=True)
            time.sleep(args.cooldown)
            ok, detail = run_one(scenario, persona)
        print(f"    {'saved ' if ok else 'FAILED: '}{detail}", flush=True)
        results.append((label, ok, detail))

    print("\nSummary:")
    for label, ok, detail in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:40} {detail}")


if __name__ == "__main__":
    main()
