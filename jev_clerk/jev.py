"""System 1: one HTTP request, several questions, a probability per answer. No text comes back."""

from __future__ import annotations

import http.client
import json
import os
import time
from urllib.parse import urlparse
from dataclasses import dataclass, field

URL = os.environ.get("TYPESAFE_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
PRICE_PER_INPUT_TOKEN = float(os.environ.get("JEV_PRICE_PER_MTOK", "0.042")) / 1e6  # measured: $0.002215 for 52,740 tokens


@dataclass
class Meter:
    calls: int = 0
    seconds: float = 0.0
    cost: float = 0.0
    input_tokens: int = 0
    log: list = field(default_factory=list)

    def snapshot(self) -> dict:
        return {"calls": self.calls, "seconds": round(self.seconds, 3), "cost": round(self.cost, 6), "input_tokens": self.input_tokens}


METER = Meter()


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


_conn: http.client.HTTPSConnection | None = None


def _post(payload: bytes) -> dict:
    """One warm TLS connection for the whole run: a fresh handshake per decision costs more than the decision."""
    global _conn
    u = urlparse(URL)
    headers = {"Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"], "Content-Type": "application/json", "Connection": "keep-alive"}
    for attempt in (0, 1):
        try:
            if _conn is None:
                _conn = http.client.HTTPSConnection(u.netloc, timeout=30)
            _conn.request("POST", u.path, body=payload, headers=headers)
            resp = _conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"jev http {resp.status}: {data[:300]!r}")
            return json.loads(data)
        except (http.client.HTTPException, ConnectionError, TimeoutError, OSError):
            _conn = None  # the server closed an idle socket: reconnect once
            if attempt:
                raise
    raise RuntimeError("unreachable")


def warm() -> None:
    ask({"ping": 1}, {"ok": noul("Is this a ping?")})


def ask(state: dict, questions: dict) -> dict:
    body = {"model": MODEL, "state": state, "questions": questions}
    started = time.perf_counter()
    out = _post(json.dumps(body).encode())
    took = time.perf_counter() - started
    usage = out.get("usage") or {}
    METER.calls += 1
    METER.seconds += took
    # OpenRouter reports a cost; the direct API reports tokens only, priced here at the rate OpenRouter billed us.
    METER.cost += float(usage.get("cost") or 0) or int(usage.get("input_tokens") or 0) * PRICE_PER_INPUT_TOKEN
    METER.input_tokens += int(usage.get("input_tokens") or 0)
    return out["answers"]
