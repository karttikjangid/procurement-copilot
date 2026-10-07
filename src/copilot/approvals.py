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


BLOCKING_ACTIONS = {"REQUEST_CLARIFICATION", "ESCALATE_MANUAL_REVIEW", "DO_NOT_PROCEED"}


def can_act(actor_id: str, role: str, request: dict, trace: dict, decided_by: dict[str, str]) -> tuple[bool, str]:
    """Who may record a decision for a role. Manager and Department Head are resolved
    from the directory; other roles have no directory mapping in the data, so any
    employee outside the requester may act, but one person may hold only one role
    per request (separation of duties)."""
    if trace.get("action") in BLOCKING_ACTIONS:
        return False, "Resolve the recommendation first (clarification, escalation or blocked vendor)."
    if actor_id == request.get("requester_id"):
        return False, "Requesters cannot approve their own request."
    requester = next((c["data"] for c in trace.get("tool_calls", []) if c["tool"] == "lookup_requester" and c.get("ok") and (c.get("data") or {}).get("found")), {})
    if role == "Manager" and (requester.get("manager") or {}).get("employee_id") not in (None, actor_id):
        return False, f"Only the requester's manager ({requester['manager']['name']}) can act as Manager."
    if role == "Department Head" and (requester.get("department_head") or {}).get("employee_id") not in (None, actor_id):
        return False, f"Only {requester['department_head']['name']} can act as Department Head."
    other_roles = [r for r, who in decided_by.items() if who == actor_id and r != role]
    if other_roles:
        return False, f"You already decided as {other_roles[0]}; one person cannot hold two approval roles."
    return True, ""


def deciders(request_id: str) -> dict[str, str]:
    events = history(request_id)
    last_run = max((i for i, e in enumerate(events) if e["action"] == "analysis_run"), default=-1)
    return {e["role"]: e["actor"] for e in events[last_run + 1:] if e["action"] in ("approved", "rejected")}
