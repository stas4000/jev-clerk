"""Turn the run ledger and the System 2 reports into the numbers the film shows. Nothing here is typed by hand."""
import json, sys, statistics as st
from pathlib import Path

root = Path(__file__).resolve().parents[1]
run = root / "runs" / (sys.argv[1] if len(sys.argv) > 1 else "demo")
rows = [json.loads(l) for l in (run / "ledger.jsonl").read_text().splitlines() if l.strip()]
truth = {t["file"]: t for t in json.loads((root / "invoices/ground_truth.json").read_text())}
blocks = []
nblocks = max(r["iteration"] for r in rows) // 10
for b in range(nblocks):
    part = [r for r in rows if b * 10 < r["iteration"] <= (b + 1) * 10]
    usd = [r for r in part if truth[r["file"]]["currency"] == "USD"]
    ok = [r for r in usd if r["verdict"]["success"]]
    steps = sum(r.get("n_steps", 0) for r in usd)
    blocks.append({
        "from": b * 10 + 1, "to": (b + 1) * 10, "version": part[0]["playbook"], "n": len(part), "usd": len(usd), "ok": len(ok),
        "flagged_ok": sum(1 for r in part if truth[r["file"]]["currency"] != "USD" and r["verdict"]["success"]), "foreign": len(part) - len(usd),
        "rate": len(ok) / max(1, len(usd)), "steps_avg": steps / max(1, len(usd)), "wasted_share": sum(r.get("n_wasted", 0) for r in usd) / max(1, steps),
        "reflex_share": sum(r.get("n_reflex", 0) for r in usd) / max(1, steps), "seconds_avg": st.mean(r["seconds"] for r in usd), "cost_avg": st.mean(r["jev_cost"] for r in part),
        "sec_ok": st.mean(r["seconds"] for r in ok) if ok else 0, "calls_ok": st.mean(r["jev_calls"] for r in ok) if ok else 0,
        "read_ok": sum(1 for r in part if all(r["verdict"]["read_ok"].values())),
    })
passes = []
for v in range(1, 12):
    p = root / f"playbooks/v{v}.report.json"
    if p.exists():
        rep = json.loads(p.read_text())
        lo, hi = (int(x) for x in rep["block"].split("-"))
        passes.append({"from": rep["from_version"], "to": rep["to_version"], "seconds": rep["seconds"], "steps_read": sum(r.get("n_steps", 0) for r in rows if lo <= r["iteration"] <= hi), "changes": [c["change"] for c in rep["changes"]], "why": [c["why"] for c in rep["changes"]]})
calls = sum(r.get("jev_calls", 0) for r in rows)
out = {"blocks": blocks, "passes": passes, "totals": {"invoices": len(rows), "jev_cost_total": sum(r.get("jev_cost", 0) for r in rows), "jev_calls": calls, "jev_cost_per_call": sum(r.get("jev_cost", 0) for r in rows) / max(1, calls), "jev_latency": sum(r.get("jev_seconds", 0) for r in rows) / max(1, calls), "system2_seconds": sum(p["seconds"] for p in passes)}}
(run / "summary.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out["blocks"], indent=1)); print(out["totals"])
