"""System 2: a deep model reads the decision log of the last block and rewrites the playbook.

It never touches the screen and never sees an invoice PDF. Its only lever is the playbook: the
wording of Jev's questions, the lessons Jev is shown, typing formats, waits, and reflexes (habits
that no longer need a Jev call). Code validates every rewrite, so the action list stays closed.

python -m jev_clerk.system2 --ledger runs/demo/ledger.jsonl --playbook playbooks/v0.json --block 1-10 --out playbooks/v1.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from .clerk_schema import ALLOWED_KEYS, FIXED_KINDS

MODEL = os.environ.get("SYSTEM2_MODEL", "claude-fable-5-1")

BRIEF = """You are System 2 in a two-speed agent that does a bookkeeper's job on a Mac.

System 1 is Jev, a decision model. It writes no text. Every step it gets a JSON state (the invoice fields, lessons, what was typed so far, the last actions, and the OCR lines of the frontmost window; each line also carries "below": the nearest text straight above it, which is a field's label or a table cell's column header, so 'the $0.00 that sits below Rate' is something Jev can see) and answers two multiple-choice questions: which KIND of action, and which on-screen LINE to click. It answers in about half a second for about $0.0001. It has no memory between steps or between invoices, and it has never seen this accounting app. Code opens a blank New Entry form before step 1 (the list's new-entry button is an icon OCR cannot read), so Jev's job starts on the empty form. Code executes the chosen action with real mouse and keyboard events. The accounting database is the referee: an invoice counts only when a submitted Purchase Invoice with the right supplier, date and amount exists.

You review the log of the last block of invoices and rewrite the PLAYBOOK, the only thing Jev is told. You may change:
- read.instructions.* and read.category_criteria.* and read.hints (list): how Jev picks the lines of the PDF.
- enter.kind_instructions, enter.item_instructions, enter.kinds.<kind> (criteria text per action; the set of kinds is closed, keep every key).
- enter.hints: short lessons shown to Jev on every step (max 25, each under 300 characters). Be concrete: name the on-screen text, the order of actions, the traps you saw in the log.
- enter.reflexes: habits executed by code WITHOUT asking Jev, straight after a given action. Each is {"after": <kind>, "do": <kind other than click_item>} or {"after": <kind>, "do": "click_text", "text": "$supplier" | "$item" | "<literal on-screen text>", "which": "last"}. "click_text" clicks the on-screen line whose text equals the value; with "which":"last" it takes the lowest match on screen (a dropdown option sits below the field that holds the same text). A reflex fires only on the step right after its "after" action. Only compile a reflex for something the log shows working the same way every time.
- enter.date_typing / enter.amount_typing: {"format": strftime using only %d %m %Y and separators (date only), "pre_keys": [...], "post_keys": [...]} with keys from: """ + ", ".join(ALLOWED_KEYS) + """. pre_keys are pressed before the text is typed, post_keys after.
- enter.settle_seconds: seconds to wait after an action kind, e.g. {"default": 0.8, "click_item": 0.6}. Range 0.25 to 3. Lower means faster, too low means Jev sees a half-drawn screen.
- enter.min_confidence (0 to 0.6): below it the engine waits and looks again instead of acting.
- enter.max_steps (10 to 60).

Read the evidence like an engineer: which steps changed nothing, where Jev looped, what the screen looked like right after typing (did the value land? did a popup open? in what format does the field show a date?), which click finally worked. Prefer fixes the log supports. Do not invent app behaviour you cannot see in the log. Keep what already works.

Answer with ONE JSON object and nothing else:
{"diagnosis": ["what went wrong or was slow, with the evidence, max 8 items"],
 "changes": [{"change": "short name", "why": "one sentence", "evidence": "iteration/step reference"}],
 "expected_effect": "one sentence",
 "playbook": { ...the complete new playbook, same structure, version incremented... }}
"""


def block_report(ledger: list[dict]) -> str:
    screens: dict[str, int] = {}
    out = []
    for log in ledger:
        v = log.get("verdict", {})
        out.append(
            f"\n## invoice #{log['iteration']} {log['file']} success={v.get('success')} steps={log.get('n_steps')} wasted={log.get('n_wasted')} "
            f"reflex={log.get('n_reflex')} seconds={log.get('seconds')} jev_calls={log.get('jev_calls')} cost=${log.get('jev_cost')}"
        )
        if log.get("error"):
            out.append(f"engine error: {log['error']}")
        out.append(f"read: {json.dumps(log.get('read'), ensure_ascii=False)}")
        out.append(f"read picked lines: {json.dumps(log.get('read_lines'), ensure_ascii=False)}  read_ok={v.get('read_ok')}")
        out.append(f"booked row: {json.dumps(log.get('row'), ensure_ascii=False)}  booked_ok={v.get('booked_ok')}")
        for s in log.get("steps", []):
            key = "\n".join(re.sub(r"\d{2}:\d{2}:\d{2}", "TIME", x) for x in s.get("screen", []))
            if key not in screens:
                screens[key] = len(screens) + 1
            out.append(
                f"  step {s['n']} [{s.get('source')}] {s.get('kind')} conf={s.get('conf')} target={s.get('target')!r}@{s.get('xy')} below={s.get('target_below')!r} "
                f"runner_up={s.get('runner_up')} -> {s.get('outcome')} | screen_changed={s.get('changed')} | saw screen S{screens[key]}" + (f" | ENGINE ABORTED: {s['aborted']}" if s.get('aborted') else "")
            )
    book = ["\n# SCREENS (OCR lines as index|text|x,y in screen points; the window is 1200x800 at 156,35)"]
    for key, sid in screens.items():
        book.append(f"\n### S{sid}\n{key}")
    return "\n".join(out) + "\n" + "\n".join(book)


def validate(new: dict, old: dict) -> dict:
    e = new["enter"]
    assert set(e["kinds"]) == set(FIXED_KINDS), "the action list is closed"
    e["hints"] = [str(h)[:300] for h in e.get("hints", [])][:25]
    clean = []
    for r in e.get("reflexes", []):
        if r.get("after") in FIXED_KINDS and (r.get("do") == "click_text" or (r.get("do") in FIXED_KINDS and r.get("do") not in ("click_item", "done"))):
            clean.append({k: r[k] for k in ("after", "do", "text", "which") if k in r})
    e["reflexes"] = clean[:12]
    for name in ("date_typing", "amount_typing"):
        t = e.setdefault(name, {})
        t["pre_keys"] = [k for k in t.get("pre_keys", []) if k in ALLOWED_KEYS][:4]
        t["post_keys"] = [k for k in t.get("post_keys", []) if k in ALLOWED_KEYS][:4]
    fmt = e["date_typing"].get("format", "%m/%d/%Y")
    assert re.fullmatch(r"(%[dmY]|[/.\- ]){3,8}", fmt) and all(x in fmt for x in ("%d", "%m", "%Y")), f"bad date format {fmt}"
    e["settle_seconds"] = {k: min(3.0, max(0.25, float(v))) for k, v in e.get("settle_seconds", {"default": 1.0}).items() if k == "default" or k in FIXED_KINDS}
    e["settle_seconds"].setdefault("default", 1.0)
    e["min_confidence"] = min(0.6, max(0.0, float(e.get("min_confidence", 0))))
    e["max_steps"] = int(min(60, max(10, int(e.get("max_steps", 40)))))
    assert set(new["read"]["instructions"]) == set(old["read"]["instructions"])
    assert set(new["read"]["category_criteria"]) == set(old["read"]["category_criteria"])
    new["version"] = int(old["version"]) + 1
    new["author"] = f"system 2 ({MODEL})"
    return new


def think(prompt: str) -> tuple[str, dict]:
    started = time.perf_counter()
    if os.environ.get("ANTHROPIC_API_KEY"):
        body = {"model": MODEL, "max_tokens": 16000, "system": BRIEF, "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=900) as resp:
            out = json.loads(resp.read())
        return out["content"][0]["text"], {"seconds": round(time.perf_counter() - started, 1), "usage": out.get("usage")}
    cmd = ["claude", "-p", "--model", MODEL, "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "", "--tools", "", "--system-prompt", BRIEF, "--output-format", "json"]
    run = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=1500)
    out = json.loads(run.stdout)
    return out["result"], {"seconds": round(time.perf_counter() - started, 1), "cost_usd": out.get("total_cost_usd"), "usage": out.get("usage")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--playbook", required=True)
    ap.add_argument("--block", required=True, help="first-last iteration, e.g. 1-10")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    lo, hi = (int(x) for x in args.block.split("-"))
    rows = [json.loads(l) for l in Path(args.ledger).read_text().splitlines() if l.strip()]
    block = [r for r in rows if lo <= r["iteration"] <= hi]
    old = json.loads(Path(args.playbook).read_text())
    board = []
    for v in sorted({r["playbook"] for r in rows}):
        part = [r for r in rows if r["playbook"] == v and r.get("steps")]
        if part:
            board.append(f"playbook v{v}: invoices {part[0]['iteration']}-{part[-1]['iteration']}, booked correctly {sum(1 for r in part if r['verdict']['success'])}/{len(part)}, avg steps {sum(r.get('n_steps', 0) for r in part) / len(part):.1f}, avg seconds {sum(r.get('seconds', 0) for r in part) / len(part):.0f}")
    prev = Path(args.playbook).with_name(f"v{old['version'] - 1}.json")
    history = "# SCOREBOARD PER PLAYBOOK VERSION\n" + "\n".join(board) + "\nIf the current version did worse than the one before it, find what your last rewrite broke and restore that part. Change as little as the evidence demands.\n\n"
    if prev.exists():
        history += f"# PREVIOUS PLAYBOOK (version {old['version'] - 1}), for comparison\n{prev.read_text()}\n\n"
    prompt = history + f"# CURRENT PLAYBOOK (version {old['version']})\n{json.dumps(old, indent=1, ensure_ascii=False)}\n\n# DECISION LOG, invoices {lo} to {hi}\n{block_report(block)}"
    text, meta = think(prompt)
    Path(args.out).with_suffix(".raw.txt").write_text(text)
    try:
        answer = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except json.JSONDecodeError as exc:  # a stray quote in a long answer: ask once for the same content as valid JSON
        fixed, meta2 = think(f"Your previous answer was not valid JSON ({exc}). Re-emit exactly the same content as one valid JSON object, escaping quotes inside strings. Previous answer:\n{text}")
        meta["repair_seconds"] = meta2["seconds"]
        meta["seconds"] = round(meta["seconds"] + meta2["seconds"], 1)
        answer = json.loads(fixed[fixed.index("{") : fixed.rindex("}") + 1])
    new = validate(answer["playbook"], old)
    Path(args.out).write_text(json.dumps(new, indent=1, ensure_ascii=False))
    report = {"block": args.block, "from_version": old["version"], "to_version": new["version"], "model": MODEL, "prompt_chars": len(prompt), **meta, "diagnosis": answer.get("diagnosis"), "changes": answer.get("changes"), "expected_effect": answer.get("expected_effect")}
    Path(args.out).with_suffix(".report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps({k: report[k] for k in ("block", "to_version", "seconds", "prompt_chars")}))
    for c in report["changes"] or []:
        print(" -", c.get("change"), "|", c.get("why"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
