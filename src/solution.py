from __future__ import annotations

from src.contracts import Architecture, ProcurementDecision
from src.copilot import data
from src.copilot.pipeline import run_copilot


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter. "single" = Architecture A, "staged" = Architecture B.
    "rules" (not in the contract) runs the deterministic checks only, as a reference row."""
    decision, _trace = run_copilot(data.get_request(request_id), architecture)
    return decision
