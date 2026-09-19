# jev-clerk

A bookkeeping clerk that works a real Mac desktop: it opens a supplier invoice PDF in Preview, reads it,
types it into [Frappe Books](https://github.com/frappe/books) (open source, desktop, no API), saves, submits, and files the PDF.

Two models, two speeds:

- **System 1, [Jev](https://docs.typesafe.ai)** makes every decision. It writes no text. Each step it gets the screen as OCR lines and answers two multiple-choice questions: which kind of action (a closed list of 12) and which line to click. About 0.35 s and $0.0002 per decision.
- **System 2, a deep model (Claude Fable 5.1 here)** never touches the screen. After every 10 invoices it reads the full decision log and rewrites the **playbook**: the wording of Jev's questions, the lessons Jev is shown, typing formats, waits, and *reflexes* (habits code runs without a model call). Code validates every rewrite, so the action list stays closed.
- **The referee is the accounting database**, opened read-only. An invoice counts only when a submitted Purchase Invoice with the right supplier, date and amount exists.

## What happened in the recorded run

30 real supplier invoices (Anthropic, Browser Use, X, Suno, Moonshot, Composio and others), worked through three times: 90 iterations, 9 blocks of 10, a System 2 pass after each block. The baseline playbook (v0) knows nothing about the app.

| invoices | playbook | booked correctly (USD invoices) | foreign currency flagged | steps / invoice | wasted steps | seconds / invoice | Jev cost / invoice |
|---|---|---|---|---|---|---|---|
| 1–10 | v0 | 0/9 | 0/1 | 5.9 | 74% | 25 s | $0.0007 |
| 11–20 | v1 | 0/8 | 2/2 | 12.8 | 41% | 44 s | $0.0018 |
| 21–30 | v2 | 0/9 | 0/1 | 39.4 | 2% | 108 s | $0.0065 |
| 31–40 | v3 | 0/9 | 1/1 | 4.0 | 100% | 19 s | $0.0006 |
| 41–50 | v4 | 0/8 | 2/2 | 13.2 | 32% | 39 s | $0.0026 |
| 51–60 | v5 | 8/9 | 1/1 | 15.0 | 0% | 44 s | $0.0032 |
| 61–70 | v6 | 9/9 | 1/1 | 15.0 | 0% | 30 s | $0.0028 |
| 71–80 | v7 | 6/8 | 2/2 | 15.0 | 0% | 30 s | $0.0025 |
| 81–90 | v8 | 9/9 | 1/1 | 15.0 | 0% | 27 s | $0.0028 |

Totals: 1141 Jev calls for $0.24, 978 s of System 2 thinking across the passes.

What the passes learned, in order (full reports in `playbooks/v*.report.json`, all written by the model from the log alone):

1. Click the input, not its label. Never type without a focused field. ₪ read as "N" by OCR means shekels.
2. The date field opens a calendar popup that swallows typing: press Escape, then type DDMMYYYY. First two reflexes.
3. The amount goes in only after clicking the `$0.00` under Rate. This rewrite also broke step one: 0 of 9.
4. The scoreboard showed v3 did worse than v2, so System 2 restored click-before-type.
5. The empty item cell reads `× Item`; Rate shows `0.00` in edit mode and must not be clicked again. First booked invoices: 8 of 9.
6. Two more reflexes (Add Row after the date, Save after the amount).
7. Waits cut, exact popup texts written into the lessons.
8. The total line never contains a month or a year.

Things that were not the model's doing, so you can judge the curve fairly:

- The engine changed during the run. Block 4 on: waits end when the screen stops changing, a warm HTTPS connection, the direct TypeSafe endpoint, a loop breaker, a per-version scoreboard for System 2, and code opens the blank New Entry form (its button is an icon, invisible to OCR). Block 5 on: every OCR line carries the label it sits below, which is what let Jev tell the Rate cell from the Amount cell. Block 7 on: in-process screen grabs (30 ms instead of 200 ms).
- The two misses in block 8 were a bug in our amount parser (it took the year in "$200.00 USD due July 30, 2026"), not a Jev mistake. Fixed before block 9.
- Blocks 4 and 5 were restarted once after engine fixes; the aborted partial blocks are kept in `runs/demo/ledger.with-aborted-*.jsonl` on the run machine and are not counted.
- Suppliers and expense items were seeded into the company file before the run. The clerk never creates master data.

## Run it

macOS 14+, Python 3.12+, [uv](https://docs.astral.sh/uv/). Grant your terminal Screen Recording and Accessibility. Install Frappe Books, create a company, add your suppliers and expense items.

```
git clone https://github.com/bles-software-org/jev-clerk && cd jev-clerk
uv sync
cp .env.example .env          # TypeSafe key; optional Anthropic key for System 2
# put inv_*.pdf in invoices/ and a ground_truth.json next to them if you want scoring
uv run jev-clerk --playbook playbooks/v0.json --start 1 --count 10 --out runs/mine
uv run jev-clerk-system2 --ledger runs/mine/ledger.jsonl --playbook playbooks/v0.json --block 1-10 --out playbooks/v1.json
```

Slam the mouse into the top-left corner to abort a live run. `playbooks/v8.json` is the playbook the run ended on.

## Layout

- `jev_clerk/desk.py` hands and eyes: real keycodes, fast screen grabs, Vision OCR, settle-until-still
- `jev_clerk/jev.py` the System 1 client, `jev_clerk/clerk.py` the loop, `jev_clerk/books.py` the read-only referee
- `jev_clerk/system2.py` the rewrite pass, `jev_clerk/clerk_schema.py` the closed vocabulary
- `jev_clerk/frontier.py` a comparison lane: a frontier model doing the same entry from bare screenshots
- `playbooks/` every playbook version with System 2's own report, `runs/demo/ledger.jsonl` every decision of the recorded run
- `video/` the film: data built from the ledger, composed in HTML, captured by frame index

Invoice PDFs are not in the repo. Screen reading builds on [typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use) (MIT).
