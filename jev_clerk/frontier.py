"""The comparison lane: a frontier model doing the same entry job from bare screenshots.

Same invoices, same app, same referee (the accounting database), same hands (desk.py). The only
difference is the brain: every step ships a screenshot to the model and waits for one JSON action.
The invoice fields are handed over already read, so this measures the ENTRY phase only.

python -m jev_clerk.frontier --start 21 --count 3 --out runs/frontier
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import time
from pathlib import Path

from . import books, desk
from .clerk import APP, relaunch, reset_to_list
from .run import load_env

MODEL = os.environ.get("FRONTIER_MODEL", "claude-opus-5")
SYSTEM = """You operate a Mac accounting app (Frappe Books) from screenshots, one action per turn.
The screenshot is 1512x982 and its pixel coordinates are the click coordinates.
Goal: enter the given supplier invoice as a new Purchase Invoice (supplier, date, one item row with quantity 1 and the amount as rate), save it and submit it.
Reply with ONE JSON object and nothing else:
{"action":"click","x":<int>,"y":<int>} | {"action":"type","text":"..."} | {"action":"key","key":"return|tab|escape|delete"} | {"action":"scroll","dy":<int>} | {"action":"done"}
Add a short "why" field."""


def ask(image_b64: str, text: str) -> tuple[dict, dict]:
    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}, {"type": "text", "text": text}]}}
    cmd = ["claude", "-p", "--model", MODEL, "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "", "--tools", "", "--system-prompt", SYSTEM, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    started = time.perf_counter()
    run = subprocess.run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True, timeout=300)
    took = time.perf_counter() - started
    result = next(json.loads(l) for l in reversed(run.stdout.splitlines()) if l.startswith("{") and '"type":"result"' in l)
    raw = result["result"]
    action = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    usage = result.get("usage") or {}
    return action, {"wall_s": round(took, 2), "api_s": round((result.get("duration_api_ms") or 0) / 1000, 2), "cost": result.get("total_cost_usd") or 0, "in": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0), "out": usage.get("output_tokens", 0)}


def enter(inv: dict, max_steps: int, shots: Path, tag: str) -> dict:
    before = books.invoice_names()
    history: list[str] = []
    steps = []
    row = None
    for n in range(1, max_steps + 1):
        desk.abort_check()
        view = desk.look()
        img = view.image.resize((1512, 982)).convert("RGB")
        img.save(shots / f"{tag}_s{n:02d}.jpg", quality=80)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        new = books.invoice_names() - before
        if new:
            row = books.invoice(sorted(new)[-1])
            if row["submitted"]:
                break
        text = f"Invoice to enter: {json.dumps(inv)}\nActions so far:\n" + "\n".join(history[-10:]) + "\nNext action?"
        try:
            action, meta = ask(base64.b64encode(buf.getvalue()).decode(), text)
        except Exception as exc:
            steps.append({"n": n, "error": repr(exc)})
            break
        kind = action.get("action")
        if kind == "click":
            desk.click(float(action["x"]), float(action["y"]))
        elif kind == "type":
            desk.type_text(str(action["text"]))
        elif kind == "key" and action.get("key") in ("return", "tab", "escape", "delete"):
            desk.press(action["key"])
        elif kind == "scroll":
            desk.scroll(-int(action.get("dy", 5)))
        steps.append({"n": n, **{k: action.get(k) for k in ("action", "x", "y", "text", "key", "why")}, **meta})
        history.append(f"{n}. {json.dumps({k: v for k, v in action.items() if k != 'why'})}")
        if kind == "done":
            break
        time.sleep(1.0)
    new = books.invoice_names() - before
    row = books.invoice(sorted(new)[-1]) if new else None
    return {"row": row, "steps": steps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default="invoices/ground_truth.json")
    ap.add_argument("--start", type=int, default=21)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--out", default="runs/frontier")
    args = ap.parse_args()
    load_env(Path(".env"))
    out = Path(args.out)
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    truth = [t for t in json.loads(Path(args.truth).read_text()) if t["currency"] == "USD"]
    picked = [t for t in truth if int(t["file"][4:6]) >= args.start][: args.count]
    for t in picked:
        iteration = int(t["file"][4:6])
        inv = {"supplier": t["supplier"], "date": t["date"], "expense_item": "AI Models & API", "quantity": 1, "amount": f"{t['amount']:.2f}"}
        reset_to_list()
        started = time.perf_counter()
        res = enter(inv, args.max_steps, shots, f"f{iteration:02d}")
        row = res["row"]
        ok = bool(row) and row["party"] == t["supplier"] and str(row["date"])[:10] == t["date"] and abs(float(row["grandTotal"]) - t["amount"]) < 0.005 and bool(row["submitted"])
        log = {"iteration": iteration, "file": t["file"], "model": MODEL, "success": ok, "row": row, "seconds": round(time.perf_counter() - started, 2), "n_steps": len(res["steps"]), "cost": round(sum(s.get("cost", 0) for s in res["steps"]), 4), "model_seconds": round(sum(s.get("api_s", 0) for s in res["steps"]), 2), "steps": res["steps"]}
        with (out / "ledger.jsonl").open("a") as fh:
            fh.write(json.dumps(log, ensure_ascii=False) + "\n")
        print(f"#{iteration:02d} success={ok} steps={log['n_steps']} {log['seconds']}s ${log['cost']}", flush=True)
        if not ok:
            relaunch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
