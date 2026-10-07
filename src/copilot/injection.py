"""Deterministic prompt-injection detector for business data (policy section 9).
It only raises a flag; the real defence is that no text can change approvals,
because approvals come from code."""
from __future__ import annotations

import re
from typing import Any

PATTERNS = [
    r"ignore (all |any |the )?(previous |prior |above |procurement |company )?(rules|instructions|polic(y|ies)|controls|guidelines)",
    r"disregard (all |any |the )?(previous |prior )?(rules|instructions|polic(y|ies)|controls)",
    r"(treat|mark|consider|record) (this|it|the request)( request)? as (already )?(cfo[- ]|ceo[- ]|vp[- ]|manager[- ]|pre[- ])?approved",
    r"\b(cfo|ceo|vp|cto|director|manager|finance|security|legal)[- ](has )?(already )?(approved|signed off)",
    r"already (been )?approved by",
    r"approve (it|this|the request)( immediately| now| right away)?",
    r"auto[- ]?approve",
    r"(bypass|skip|override|waive) (all |the )?(security|privacy|legal|procurement|review|controls|polic(y|ies)|approvals?)",
    r"do not (flag|escalate|route|review|check)",
    r"(system|developer) (prompt|message|instruction)s?\s*:",
    r"^\s*system\s*:",
    r"you are now",
    r"new instructions",
    r"(reveal|print|show|expose) (the |your )?(system prompt|api key|secrets?|credentials)",
    r"pre[- ]?approved vendor[,.]? skip",
]
_COMPILED = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in PATTERNS]


def scan_text(text: Any) -> list[str]:
    if not text:
        return []
    s = str(text)
    hits = []
    for rx in _COMPILED:
        m = rx.search(s)
        if m:
            start = max(0, m.start() - 20)
            hits.append(s[start:m.end() + 40].strip())
    return hits


def scan_texts(fields: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for name, value in fields.items():
        for snippet in scan_text(value)[:1]:
            out.append({"field": name, "snippet": snippet[:160]})
    return out
