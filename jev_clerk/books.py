"""Read-only view of the Frappe Books company file. The UI is the only writer; this is the referee."""

from __future__ import annotations

import os
import sqlite3

DB = os.environ.get("CLERK_BOOKS_DB", os.path.expanduser("~/Documents/Frappe Books/Bles Software.books.db"))


def _q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def invoice_names() -> set[str]:
    return {r["name"] for r in _q("select name from PurchaseInvoice")}


def invoice(name: str) -> dict:
    row = _q("select name, party, date, grandTotal, submitted, cancelled from PurchaseInvoice where name=?", (name,))[0]
    items = _q("select item, rate, quantity, amount from PurchaseInvoiceItem where parent=? order by idx", (name,))
    return {**dict(row), "items": [dict(i) for i in items]}


def suppliers() -> list[str]:
    return [r["name"] for r in _q("select name from Party where role in ('Supplier','Both') order by name")]


def items() -> list[str]:
    return [r["name"] for r in _q("select name from Item order by name")]
