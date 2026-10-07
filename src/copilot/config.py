"""Runtime configuration. Everything is read at call time so tests and hidden
evaluation data can override it through environment variables."""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(ROOT / "data")))


def reference_date() -> date:
    """Policy reference date. Never the machine clock (README + policy header).

    Order: REFERENCE_DATE env var, then the date written in procurement_policy.md,
    then data/README.md. Fails loudly if none is found instead of guessing.
    """
    override = os.getenv("REFERENCE_DATE")
    if override:
        return date.fromisoformat(override.strip())
    pattern = re.compile(r"reference date[^0-9]{0,40}(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
    for name in ("procurement_policy.md", "README.md"):
        path = data_dir() / name
        if path.is_file():
            match = pattern.search(path.read_text(encoding="utf-8"))
            if match:
                return date.fromisoformat(match.group(1))
    raise RuntimeError("No policy reference date found; set REFERENCE_DATE explicitly.")


def vendor_risk_base_url() -> str:
    return os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


def model_name() -> str:
    return os.getenv("MODEL_NAME", "gemini-3.8-flash")


def llm_mode() -> str:
    """live: call the API and record; replay: only use recordings; off: no LLM."""
    return os.getenv("LLM_MODE", "live").strip().lower()


SECURITY_REVIEW_VALID_DAYS = 365
MAX_AGENT_TOOL_CALLS = 12
