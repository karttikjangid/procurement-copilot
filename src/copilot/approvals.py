"""Human approval workflow and audit log (SQLite). The copilot never writes an
approval; only a named human acting in the UI does."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.copilot.config import ROOT

DB = ROOT / "runs" / "approvals.db"


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT, request_id TEXT, actor TEXT,
                 role TEXT, action TEXT, reason TEXT, payload TEXT)""")
    return c


def log(request_id: str, actor: str, role: str, action: str, reason: str = "", payload: dict | None = None) -> None:
    with _conn() as c:
        c.execute("INSERT INTO audit (at, request_id, actor, role, action, reason, payload) VALUES (?,?,?,?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(timespec="seconds"), request_id, actor, role, action, reason,
                   json.dumps(payload or {}, default=str)))


def history(request_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT at, actor, role, action, reason FROM audit WHERE request_id=? ORDER BY id", (request_id,)).fetchall()
    return [dict(zip(["at", "actor", "role", "action", "reason"], r)) for r in rows]


def status(request_id: str, required: list[str]) -> tuple[str, dict[str, str]]:
    """Latest human decision per role since the most recent analysis run."""
    events = history(request_id)
    last_run = max((i for i, e in enumerate(events) if e["action"] == "analysis_run"), default=-1)
    decisions: dict[str, str] = {}
    for e in events[last_run + 1:]:
        if e["action"] in ("approved", "rejected"):
            decisions[e["role"]] = e["action"]
    if any(v == "rejected" for v in decisions.values()):
        return "Rejected", decisions
    if required and all(decisions.get(r) == "approved" for r in required):
        return "Approved by all required humans", decisions
    return "Awaiting human decisions", decisions
