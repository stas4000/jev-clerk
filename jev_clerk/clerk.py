"""The clerk: read one invoice PDF in Preview, enter it into Frappe Books through the UI, file the PDF.

Jev (System 1) makes every decision. Code owns execution, and the accounting database is the referee.
Everything Jev is told lives in the playbook, which is the only thing System 2 may rewrite.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from PIL import ImageChops

from . import books, desk, jev
from .clerk_schema import ALLOWED_KEYS, FIXED_KINDS, TYPE_KINDS

APP = "Frappe Books"
MAX_LINES = 110


@dataclass
class Invoice:
    file: str
    supplier: str = ""
    number: str = ""
    date: str = ""  # ISO
    amount: float = 0.0
    currency: str = ""
    category: str = ""
    confidence: dict = field(default_factory=dict)


# ------------------------------------------------------------------ reading


def label_above(line: desk.Line, lines: list[desk.Line]) -> str:
    """The nearest text straight above a line: a field's label, or a table cell's column header."""
    best = None
    for c in lines:
        if c.i == line.i or not (4 < line.y - c.y < 120):
            continue
        if abs(c.x - line.x) > max(c.w, line.w) / 2 + 24:
            continue
        if best is None or c.y > best.y:
            best = c
    return best.text[:40] if best else ""


def lines_state(view: desk.View) -> tuple[list[dict], dict[str, str]]:
    lines = view.lines[:MAX_LINES]
    above = {l.i: label_above(l, lines) for l in lines}
    state = [{"i": l.i, "text": l.text, **({"below": above[l.i]} if above[l.i] else {})} for l in lines]
    criteria = {str(l.i): l.text[:80] + (f"  (sits below '{above[l.i]}')" if above[l.i] else "") for l in lines}
    return state, criteria


def parse_date(text: str, order: str) -> str:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return m.group(0)
    text = re.sub(r"(?i)date( of issue| due)?|issued|invoice", " ", text)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        m = re.search(r"[A-Za-z]{3,9} \d{1,2}, \d{4}" if fmt[1] in "Bb" else r"\d{1,2} [A-Za-z]{3,9} \d{4}", text)
        if m:
            try:
                return datetime.strptime(m.group(0), fmt).date().isoformat()
            except ValueError:
                continue
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        d, mo = (a, b) if order == "day_first" else (b, a)
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return ""
    return ""


def parse_amount(text: str) -> float:
    money = re.findall(r"\d[\d,]*\.\d{2}", text)  # "$200.00 USD due July 30, 2026": the amount has cents, the year does not
    if money:
        return float(money[0].replace(",", ""))
    found = re.findall(r"\d[\d,]*", text)
    return float(found[-1].replace(",", "")) if found else 0.0


def parse_number(text: str) -> str:
    tokens = [t.strip(".,:;#") for t in text.split()]
    tokens = [t for t in tokens if any(c.isdigit() for c in t) and len(t) >= 4]
    return max(tokens, key=len) if tokens else ""


def read_invoice(pdf: Path, playbook: dict, log: dict) -> Invoice:
    """Open the PDF in Preview, OCR the window, and let Jev point at the lines that matter."""
    subprocess.run(["open", "-a", "Preview", str(pdf)], check=True)
    for _ in range(12):  # wait for Preview to be in front and drawn, not for a fixed time
        desk.settle(1.2, floor=0.25)
        view = desk.look()
        if view.app == "Preview" and len(view.lines) > 8:
            break
    state, criteria = lines_state(view)
    ins = playbook["read"]["instructions"]
    suppliers = books.suppliers()
    questions = {
        "supplier": jev.choice(ins["supplier"], criteria),
        "number": jev.choice(ins["number"], criteria),
        "date": jev.choice(ins["date"], criteria),
        "total": jev.choice(ins["total"], criteria),
        "date_order": jev.choice(ins["date_order"], {"month_name": "The month is written as a word.", "day_first": "Digits only, day before month.", "month_first": "Digits only, month before day.", "iso": "Year first."}),
        "currency": jev.choice(ins["currency"], {"USD": "US dollars ($, US$, USD).", "ILS": "Israeli shekels (₪, NIS, ILS).", "EUR": "Euros (€, EUR).", "other": "Any other currency."}),
        "category": jev.choice(ins["category"], playbook["read"]["category_criteria"]),
        "known_supplier": jev.choice("Which supplier on file issued this invoice?", {**{s: s for s in suppliers}, "none": "None of the suppliers on file."}),
    }
    answers = jev.ask({"task": "Read a supplier invoice that is open on screen.", "hints": playbook["read"].get("hints", []), "screen_lines": state}, questions)
    by_i = {str(l.i): l.text for l in view.lines}
    pick = {k: by_i.get(answers[k]["choice"], "") for k in ("supplier", "number", "date", "total")}
    inv = Invoice(
        file=pdf.name,
        supplier=answers["known_supplier"]["choice"] if answers["known_supplier"]["choice"] != "none" else pick["supplier"],
        number=parse_number(pick["number"]),
        date=parse_date(pick["date"], answers["date_order"]["choice"]),
        amount=parse_amount(pick["total"]),
        currency=answers["currency"]["choice"],
        category=answers["category"]["choice"],
        confidence={k: round(v.get("confidence", 0), 2) for k, v in answers.items()},
    )
    log.update({"read_lines": pick, "read_seconds": round(view.seconds, 2), "screen": [l.text for l in view.lines[:MAX_LINES]]})
    desk.press("w", command=True)
    time.sleep(0.15)
    return inv


# ------------------------------------------------------------------ entering


def value_for(kind: str, inv: Invoice, playbook: dict) -> str:
    if kind == "type_supplier":
        return inv.supplier
    if kind == "type_item":
        return inv.category
    if kind == "type_amount":
        return f"{inv.amount:.2f}"
    return datetime.strptime(inv.date, "%Y-%m-%d").strftime(playbook["enter"]["date_typing"]["format"])


def press_all(keys: list[str]) -> None:
    for k in keys:
        if k in ALLOWED_KEYS:
            desk.press(k)
            time.sleep(0.25)


def perform(kind: str, target: desk.Line | None, inv: Invoice, playbook: dict) -> str:
    enter = playbook["enter"]
    if kind == "click_item" and target:
        desk.click(target.x, target.y)
        return f"clicked {target.text!r}"
    if kind in TYPE_KINDS:
        typing = enter.get(kind.replace("type_", "") + "_typing", {})
        press_all(typing.get("pre_keys", []))
        text = value_for(kind, inv, playbook)
        desk.type_text(text)
        time.sleep(0.2)
        press_all(typing.get("post_keys", []))
        return f"typed {text!r}"
    if kind.startswith("press_"):
        desk.press(kind.removeprefix("press_"))
        return kind
    if kind.startswith("scroll_"):
        desk.scroll(-6 if kind == "scroll_down" else 6)
        return kind
    if kind == "wait":
        time.sleep(0.8)
        return "waited"
    return kind


def reflex_for(last_kind: str, view: desk.View, inv: Invoice, playbook: dict) -> tuple[str, desk.Line | None] | None:
    """A reflex is a habit System 2 compiled from the log: after X, do Y, without asking Jev."""
    for r in playbook["enter"].get("reflexes", []):
        if r.get("after") != last_kind:
            continue
        if r.get("do") == "click_text":
            want = {"$supplier": inv.supplier, "$item": inv.category}.get(r.get("text", ""), r.get("text", ""))
            hits = [l for l in view.lines if l.text.strip().lower() == want.strip().lower()]
            if r.get("which") == "last":
                hits = hits[-1:]
            if hits and (r.get("which") == "last" or len(hits) >= int(r.get("min_matches", 1))):
                return "click_item", hits[-1] if r.get("which") == "last" else hits[int(r.get("index", 0)) if int(r.get("index", 0)) < len(hits) else -1]
        elif r.get("do") in FIXED_KINDS and r.get("do") != "click_item":
            return r["do"], None
    return None


def reset_to_list() -> None:
    """Engine housekeeping between invoices, never counted as a step: back to the Purchase Invoices list."""
    desk.activate(APP)
    desk.settle(0.8)
    desk.press("escape")
    time.sleep(0.15)
    for _ in range(2):
        view = desk.look()
        hit = next((l for l in view.lines if l.text.strip() == "Purchase Invoices" and l.x < 400), None)
        if hit:
            desk.click(hit.x, hit.y)
            desk.settle(1.5)
            return
        hit = next((l for l in view.lines if l.text.strip() == "Purchases" and l.x < 400), None)
        if hit:
            desk.click(hit.x, hit.y)
            time.sleep(0.8)


def open_new_entry() -> bool:
    """Fixture, not a decision: every invoice starts on a blank New Entry form.

    The list's new-entry control is an icon with no text, which OCR cannot see (the known blind spot of
    text-only perception), so code presses it: 'Make Entry' on an empty list, else the '+' at the window's top right."""
    view = desk.look()
    hit = next((l for l in view.lines if l.text.strip() == "Make Entry"), None)
    if hit:
        desk.click(hit.x, hit.y)
    else:
        from typesafe_computer_use import macos

        win = macos.frontmost_window_bounds()
        if not win:
            return False
        desk.click(win[0] + win[2] - 38, win[1] + 31)
    desk.settle(2.0)
    return True


def relaunch() -> None:
    subprocess.run(["pkill", "-x", APP])
    time.sleep(1.5)
    subprocess.run(["open", "-a", APP])
    time.sleep(7)


def enter_invoice(inv: Invoice, playbook: dict, steps_log: list, shots: Path | None, tag: str) -> dict:
    enter = playbook["enter"]
    before = books.invoice_names()
    history: list[str] = []
    typed: dict[str, str] = {}
    last_kind, last_texts, last_thumb, stale = "", None, None, 0
    draft = None
    for n in range(1, int(enter.get("max_steps", 40)) + 1):
        desk.abort_check()
        view = desk.look()
        if shots:
            view.image.resize((1512, 982)).convert("RGB").save(shots / f"{tag}_s{n:02d}.jpg", quality=80)
        texts = [l.text for l in view.lines]
        thumb = view.image.convert("L").resize((160, 104))
        moved = last_thumb is not None and sum(ImageChops.difference(thumb, last_thumb).histogram()[24:]) > 12
        changed = texts != last_texts or moved
        last_thumb = thumb
        stale = 0 if changed else stale + 1
        if history:
            history[-1] += " -> screen changed" if changed else " -> NOTHING changed"
            steps_log[-1]["changed"] = changed
        last_texts = texts
        new = books.invoice_names() - before
        if new:
            draft = books.invoice(sorted(new)[-1])
            if draft["submitted"]:
                break
        if stale >= 4:
            break
        recent = [(x.get("kind"), x.get("target")) for x in steps_log[-12:]]
        if len(recent) == 12 and len(set(recent)) <= 4:  # going round in circles: stop paying for it, let System 2 read why
            steps_log[-1]["aborted"] = "looping"
            break
        entry = {"n": n, "t": round(time.time(), 2), "look_s": round(view.seconds, 2)}
        reflex = reflex_for(last_kind, view, inv, playbook) if changed or last_kind in TYPE_KINDS else None
        if reflex:
            kind, target = reflex
            entry.update({"source": "reflex", "kind": kind, "conf": 1.0})
        else:
            state, criteria = lines_state(view)
            questions = {"kind": jev.choice(enter["kind_instructions"], {k: enter["kinds"].get(k, k) for k in FIXED_KINDS}), "item": jev.choice(enter["item_instructions"], criteria)}
            t0 = time.perf_counter()
            answers = jev.ask(
                {
                    "goal": "A blank New Entry purchase invoice form is open. Fill in this supplier invoice, save it and submit it.",
                    "invoice": {"supplier": inv.supplier, "date": inv.date, "expense_item": inv.category, "quantity": 1, "amount": f"{inv.amount:.2f}"},
                    "lessons_from_earlier_runs": enter.get("hints", []),
                    "typed_so_far": typed,
                    "saved_as_draft": bool(draft),
                    "previous_actions": history[-8:],
                    "screen_lines": state,
                },
                questions,
            )
            kind = answers["kind"]["choice"]
            conf = answers["kind"].get("confidence", 0)
            target = None
            if kind == "click_item":
                target = next((l for l in view.lines if str(l.i) == answers["item"]["choice"]), None)
                conf = min(conf, answers["item"].get("confidence", 0))
            entry.update({"source": "jev", "kind": kind, "conf": round(conf, 2), "jev_s": round(time.perf_counter() - t0, 2)})
            probs = answers["kind"].get("probabilities") or {}
            entry["runner_up"] = sorted(probs.items(), key=lambda kv: -kv[1])[1][0] if len(probs) > 1 else ""
            if conf < float(enter.get("min_confidence", 0)):
                kind, target = "wait", None
                entry["gated"] = True
        if kind == "done":
            entry["outcome"] = "done"
            steps_log.append(entry)
            break
        entry["t_act"] = round(time.time(), 2)
        outcome = perform(kind, target, inv, playbook)
        if kind in TYPE_KINDS:
            typed[kind.removeprefix("type_")] = value_for(kind, inv, playbook)
        entry.update({"target": target.text if target else "", "xy": [round(target.x), round(target.y)] if target else None, "outcome": outcome, "target_below": label_above(target, view.lines) if target else "", "screen": [f"{l.i}|{l.text}|{round(l.x)},{round(l.y)}" for l in view.lines[:MAX_LINES]]})
        steps_log.append(entry)
        history.append(f"{n}. {outcome}")
        last_kind = kind
        entry["settle_s"] = round(desk.settle(float(enter["settle_seconds"].get(kind, enter["settle_seconds"].get("default", 1.0)))), 2)
    new = books.invoice_names() - before
    draft = books.invoice(sorted(new)[-1]) if new else None
    return {"row": draft, "steps": len(steps_log)}


# ------------------------------------------------------------------ one iteration


def score(inv: Invoice, truth: dict, row: dict | None) -> dict:
    read_ok = {
        "supplier": inv.supplier == truth["supplier"],
        "number": inv.number == truth["number"],
        "date": inv.date == truth["date"],
        "amount": abs(inv.amount - truth["amount"]) < 0.005,
        "currency": inv.currency == truth["currency"],
    }
    if truth["currency"] != "USD":
        return {"read_ok": read_ok, "booked_ok": None, "success": inv.currency == truth["currency"] and row is None}
    booked = bool(row) and {
        "supplier": row["party"] == truth["supplier"],
        "date": str(row["date"])[:10] == truth["date"],
        "amount": abs(float(row["grandTotal"]) - truth["amount"]) < 0.005,
        "submitted": bool(row["submitted"]),
    }
    return {"read_ok": read_ok, "booked_ok": booked or None, "success": bool(booked) and all(booked.values())}


def file_pdf(pdf: Path, inv: Invoice, root: Path, status: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", inv.supplier).strip("-") or "unknown"
    folder = root / ("review" if status != "booked" else f"filed/{inv.date[:7] or 'undated'}")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{safe}_{inv.number or pdf.stem}.pdf"
    shutil.copy2(pdf, dest)
    return str(dest.relative_to(root))


def run_one(pdf: Path, truth: dict, playbook: dict, root: Path, iteration: int, shots: Path | None) -> dict:
    jev_before = jev.METER.snapshot()
    started = time.perf_counter()
    log: dict = {"iteration": iteration, "file": pdf.name, "playbook": playbook["version"], "t0": round(time.time(), 2), "steps": []}
    inv = read_invoice(pdf, playbook, log)
    log["read"] = asdict(inv)
    log["t_read_done"] = round(time.time(), 2)
    row = None
    if inv.currency == "USD" and inv.date and inv.amount:
        reset_to_list()
        log["form_opened"] = open_new_entry()
        result = enter_invoice(inv, playbook, log["steps"], shots, f"i{iteration:02d}")
        row = result["row"]
    else:
        log["skipped_entry"] = "foreign currency or unreadable: routed to review"
    verdict = score(inv, truth, row)
    status = "booked" if (row and row["submitted"]) else "review"
    filed = file_pdf(pdf, inv, root, status)
    after = jev.METER.snapshot()
    log.update(
        {
            "row": row,
            "verdict": verdict,
            "filed": filed,
            "seconds": round(time.perf_counter() - started, 2),
            "jev_calls": after["calls"] - jev_before["calls"],
            "jev_cost": round(after["cost"] - jev_before["cost"], 6),
            "jev_seconds": round(after["seconds"] - jev_before["seconds"], 2),
            "n_steps": len(log["steps"]),
            "n_reflex": sum(1 for s in log["steps"] if s.get("source") == "reflex"),
            "n_wasted": sum(1 for s in log["steps"] if s.get("changed") is False),
        }
    )
    with (root / "tracking.csv").open("a", newline="") as fh:
        csv.writer(fh).writerow([iteration, pdf.name, inv.supplier, inv.number, inv.date, inv.amount, inv.currency, inv.category, status, (row or {}).get("name", ""), filed])
    if not verdict["success"] and row is None and inv.currency == "USD":
        relaunch()  # a half-filled form must not leak into the next invoice
    return log
