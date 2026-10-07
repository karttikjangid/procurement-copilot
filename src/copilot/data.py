"""Fresh, typed reads of the business data. Loaded on every call so a swapped
data directory (hidden evaluation cases) is picked up without restarts."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from src.copilot.config import data_dir


def _csv(name: str) -> list[dict[str, str]]:
    with (data_dir() / name).open(encoding="utf-8", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def employees() -> list[dict[str, str]]:
    return _csv("employees.csv")


def budgets() -> list[dict[str, str]]:
    return _csv("department_budgets.csv")


def catalog() -> list[dict[str, str]]:
    return _csv("software_catalog.csv")


def vendors() -> list[dict[str, str]]:
    return _csv("vendors.csv")


def purchase_history() -> list[dict[str, str]]:
    return _csv("purchase_history.csv")


def requests() -> list[dict]:
    return json.loads((data_dir() / "requests.json").read_text(encoding="utf-8"))


def get_request(request_id: str) -> dict:
    for req in requests():
        if str(req.get("request_id")) == str(request_id):
            return req
    raise KeyError(f"Unknown request_id: {request_id}")


def policy_text() -> str:
    return (data_dir() / "procurement_policy.md").read_text(encoding="utf-8")


_SUFFIXES = re.compile(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|gmbh|plc)\b\.?", re.I)


def norm_name(value: object) -> str:
    """Normalise vendor/product names: case, punctuation, legal suffixes, domains."""
    text = str(value or "").casefold()
    text = re.sub(r"\.(com|io|ai|co|net|org)\b", "", text)
    text = _SUFFIXES.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())
