"""ledger + reports -> video/data.js. Every number in the film comes from here."""
import json, os, subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / "video/build_data.py"), "demo"], check=True, capture_output=True)
S = json.loads((root / "runs/demo/summary.json").read_text())
rows = {r["iteration"]: r for r in map(json.loads, (root / "runs/demo/ledger.jsonl").read_text().splitlines())}
HERO = 85
h = rows[HERO]; clip0 = h["t0"] - 0.3
events = []
for s in h["steps"]:
    detail = s.get("target") or s.get("outcome", "")
    if s.get("target_below"): detail += f"   · below “{s['target_below']}”"
    events.append({"t": round(s.get("t_act", s["t"]) - clip0, 2), "kind": s["kind"], "detail": detail, "conf": s.get("conf", 1), "source": s["source"], "cost": (h["jev_cost"] - 0.00027) / max(1, h["jev_calls"] - 1) if s["source"] == "jev" else 0})
first, save = events[0]["t"], next(e["t"] for e in events if "Save" in e["detail"])
caps = [{"t": 0, "html": "Jev reads the PDF. <span class='dim'>Eight questions, one call, 0.4 s.</span>"},
        {"t": first - 0.4, "html": "One choice per step, from a closed list of 12 actions."},
        {"t": events[5]["t"] - 0.2, "html": "<span class='s2'>Violet steps are reflexes:</span> habits System 2 compiled. No model call."},
        {"t": save - 0.2, "html": "Save. Confirm. Submit."},
        {"t": events[-1]["t"] + 1.0, "html": f"Booked as {h['row']['name']}. <span class='s1'>The database agrees.</span>"}]
LEARNED = {1: "Click the input, not its label. Never type without a focused field.", 2: "A calendar popup blocks typing: press Escape, then type DDMMYYYY. Two reflexes compiled.",
           3: "Amount only after clicking the $0.00 cell under Rate. (This rewrite also broke the first step.)", 4: "The scoreboard showed v3 did worse. Restored click-before-type.",
           5: "The empty item cell reads “× Item”. Rate shows “0.00” in edit mode: type, never click again.", 6: "Two more reflexes: Add Row after the date, Save after the amount.",
           7: "Waits cut to 0.8 s. Exact popup texts written into the lessons.", 8: "The total line never contains a month or a year."}
for p in S["passes"]: p["learned"] = LEARNED.get(p["to"], p["changes"][0])
fr = None; fp = root / "runs/frontier/ledger.jsonl"
if fp.exists():
    F = [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]
    if F:
        n = len(F); steps = sum(f["n_steps"] for f in F)
        fr = {"label": "Claude Opus 5 · screenshots", "n": n, "ok": sum(1 for f in F if f["success"]), "cost_avg": sum(f["cost"] for f in F) / n, "steps_avg": steps / n,
              "model_sec_avg": sum(f["model_seconds"] for f in F) / n, "sec_per_step": sum(f["model_seconds"] for f in F) / max(1, steps),
              "note": f"Opus 5 measured on {n} of the same invoices, entry phase only, invoice fields handed over already read. Cost is the CLI's reported API-equivalent cost. Opus time is model latency only; its screenshots travelled through a relay we do not count. Jev numbers: block 9 of the run ledger."}
last = S["blocks"][-1]
jev_model_sec = sum(r["jev_seconds"] for r in rows.values() if r["iteration"] > 80 and r.get("n_steps")) / max(1, sum(1 for r in rows.values() if r["iteration"] > 80 and r.get("n_steps")))
data = {**S, "hero": {"iteration": HERO, "playbook": h["playbook"], "frames": len(os.listdir(root / "video/frames/hero")), "speed": 1, "read": h["read"], "row": h["row"]["name"], "events": events, "captions": caps, "read_cost": 0.00027, "read_seconds": 0, "cost": h["jev_cost"], "seconds": h["seconds"]},
        "montage_frames": [240, 240, 240], "montage_speed": 8, "frontier": fr, "jev_model_sec": jev_model_sec, "repo": "bles-software-org/jev-clerk"}
(root / "video/data.js").write_text("window.DATA = " + json.dumps(data, ensure_ascii=False) + ";")
print(len(events), "events; blocks", [(b["ok"], b["usd"]) for b in S["blocks"]], "frontier", fr and {k: fr[k] for k in ("n", "ok", "cost_avg", "steps_avg", "model_sec_avg")}, "jev model s/invoice", round(jev_model_sec, 2))
