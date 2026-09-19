"""python -m jev_clerk.run --playbook playbooks/v0.json --start 1 --count 10 --out runs/demo"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def load_env(path: Path) -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoices", default="invoices")
    ap.add_argument("--truth", default="invoices/ground_truth.json")
    ap.add_argument("--playbook", required=True)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--out", default="runs/demo")
    ap.add_argument("--no-shots", action="store_true")
    args = ap.parse_args()
    load_env(Path(".env"))
    from . import clerk, jev  # after the env is loaded

    out = Path(args.out)
    shots = None if args.no_shots else out / "shots"
    (shots or out).mkdir(parents=True, exist_ok=True)
    playbook = json.loads(Path(args.playbook).read_text())
    truth = {t["file"]: t for t in json.loads(Path(args.truth).read_text())}
    every = sorted(Path(args.invoices).glob("inv_*.pdf"))
    for offset in range(args.count):
        iteration = args.start + offset
        pdf = every[(iteration - 1) % len(every)]  # past the last invoice the pile starts over: a second epoch
        try:
            log = clerk.run_one(pdf, truth[pdf.name], playbook, out, iteration, shots)
        except Exception as exc:  # one broken invoice must not end the block
            log = {"iteration": iteration, "file": pdf.name, "playbook": playbook["version"], "error": repr(exc), "verdict": {"success": False}, "steps": []}
            clerk.relaunch()
        log["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with (out / "ledger.jsonl").open("a") as fh:
            fh.write(json.dumps(log, ensure_ascii=False) + "\n")
        v = log["verdict"]
        print(f"#{iteration:02d} {pdf.name} success={v['success']} steps={log.get('n_steps')} wasted={log.get('n_wasted')} reflex={log.get('n_reflex')} {log.get('seconds')}s ${log.get('jev_cost')}", flush=True)
    print(json.dumps(jev.METER.snapshot()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
