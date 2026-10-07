"""Shared guardrail: runs the chosen architecture, then makes the final decision
in code. Identical for A and B, so the experiment isolates orchestration."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from src.contracts import EvidenceItem, ProcurementDecision, RunTelemetry
from src.copilot import agents
from src.copilot.llm import GeminiClient, LLMStats, LLMUnavailable
from src.copilot.policy import ACTION_LABELS, PolicyResult, Signals, evaluate, facts_from_calls
from src.copilot.tools import ToolBox, record

LAST_TRACES: dict[str, dict[str, Any]] = {}

APPROVAL_CLAIM = re.compile(
    r"\b(is|are|has been|have been|was|already) (pre-?)?approved\b|\bapprove (this|it|the request)\b|auto-?approv|"
    r"\bno (further )?(review|approval)s? (is |are )?(needed|required)\b|\bcan (be )?(proceed|purchased) without\b|"
    r"\b(not|no|isn't|aren't|doesn't|don't|never)\b[^.]{0,50}\b(required|needed|necessary|need)\b|"
    r"\b(skip|skipped|waive|waived|bypass|omit|omitted|unnecessary)\b",
    re.IGNORECASE)


def _canonical_args(request: dict, tb: ToolBox) -> dict[str, dict[str, Any]]:
    req = tb.latest("lookup_requester")
    dept = ((req.data or {}).get("department") if req and req.ok and req.args.get("employee_id") == request.get("requester_id") else None)
    if dept is None:
        dept = ""
    return {
        "lookup_requester": {"employee_id": request.get("requester_id")},
        "check_budget": {"department": dept, "annual_cost_usd": request.get("annual_cost_usd")},
        "search_catalog": {"product_name": request.get("product_name") or "", "vendor_name": request.get("vendor_name") or "",
                           "category": request.get("category") or ""},
        "get_purchase_history": {"department": dept, "vendor_name": request.get("vendor_name") or "", "request_id": request.get("request_id") or ""},
        "get_vendor_registry": {"vendor_name": request.get("vendor_name") or ""},
        "get_vendor_risk": {"vendor_name": request.get("vendor_name") or ""},
    }


def _same(a: Any, b: Any) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a or "").strip().casefold() == str(b or "").strip().casefold()


def ensure_evidence(request: dict, tb: ToolBox) -> list[str]:
    """Guardrail step: every required tool must have run with the request's real
    arguments. Anything the agent skipped or called with other arguments is re-run
    by code; each case is logged as an agent defect."""
    defects = []
    for tool in agents.GATHER_TOOLS:
        canonical = _canonical_args(request, tb)[tool]
        calls = [c for c in tb.calls if c.tool == tool]
        good = [c for c in calls if all(_same(c.args.get(k), v) for k, v in canonical.items())]
        if not calls:
            defects.append(f"agent skipped {tool}")
        elif not good:
            defects.append(f"agent called {tool} with arguments that do not match the request: {calls[-1].args}")
        if not good:
            tb.call(tool, **canonical)
        elif good[-1] is not calls[-1]:
            tb.calls.append(tb.calls.pop(tb.calls.index(good[-1])))  # make the canonical call the latest
    return defects


def signals_from(raw: dict | None) -> Signals:
    if not raw:
        return Signals()
    gap = str(raw.get("overlap_gap_credible", "")).lower()
    return Signals(
        extra_data_classes=set(raw.get("extra_data_classes") or []),
        injection_detected=bool(raw.get("injection_detected")),
        injection_quote=str(raw.get("injection_quote") or ""),
        business_purpose_clear=raw.get("business_purpose_clear") if isinstance(raw.get("business_purpose_clear"), bool) else None,
        overlap_gap_credible={"yes": True, "no": False}.get(gap),
    )


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    out = set()
    for n in _NUM.findall(text):
        n = n.replace(",", "")
        out.add(n[:-2] if n.endswith(".0") else n)
    return out


def check_claims(claims: list[dict] | None, tb: ToolBox) -> tuple[list[dict], list[dict]]:
    """A claim is grounded when its call_id exists and every number/date in it
    appears in that tool's output."""
    by_id = {c.call_id: c for c in tb.calls}
    grounded, ungrounded = [], []
    for claim in claims or []:
        call = by_id.get(str(claim.get("call_id", "")).strip())
        finding = str(claim.get("finding", ""))
        if call is None:
            ungrounded.append({**claim, "reason": "unknown call_id"})
            continue
        blob = json.dumps(call.data, default=str) + " " + str(call.error or "")
        source_numbers = _numbers(blob) | {n.replace("-", "") for n in re.findall(r"\d{4}-\d{2}-\d{2}", blob)}
        missing = [n for n in _numbers(finding) if n not in source_numbers and n.replace("-", "") not in source_numbers]
        # dates in a claim are split by _numbers into parts; check whole dates too
        dates_ok = all(d in blob for d in re.findall(r"\d{4}-\d{2}-\d{2}", finding))
        if missing or not dates_ok:
            ungrounded.append({**claim, "reason": f"values not found in {call.call_id}: {missing or 'date'}"})
        else:
            grounded.append({**claim, "tool": call.tool})
    return grounded, ungrounded


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "unknown"


def build_evidence(tb: ToolBox, policy: PolicyResult) -> list[EvidenceItem]:
    ev: list[EvidenceItem] = []
    latest = {c.tool: c for c in tb.calls}
    c = latest.get("lookup_requester")
    if c:
        d = c.data or {}
        if d.get("found"):
            head = d.get("department_head") or {}
            mgr = d.get("manager") or {}
            ev.append(EvidenceItem(source="lookup_requester", reference=f"employees.csv:{d['employee_id']} ({c.call_id})",
                                   finding=f"Requester {d['name']} ({d['employee_id']}), {d['department']}; manager {mgr.get('name', 'none')}; "
                                           f"department head for routing {head.get('name', 'not found')} ({head.get('basis', '')})"))
        else:
            ev.append(EvidenceItem(source="lookup_requester", reference=c.call_id, finding=f"Requester id {c.args.get('employee_id')} not found in employee directory"))
    c = latest.get("check_budget")
    if c and c.data:
        d = c.data
        if d.get("found"):
            status = ("exceeds" if d["within_available_budget"] is False else "is within" if d["within_available_budget"] else "cannot be compared with")
            ev.append(EvidenceItem(source="check_budget", reference=f"department_budgets.csv:{d['department']} ({c.call_id})",
                                   finding=f"{d['department']} available software budget {_money(d['available_usd'])} "
                                           f"(annual {_money(d['annual_software_budget_usd'])}, committed {_money(d['committed_usd'])}); "
                                           f"requested {_money(d['requested_annual_cost_usd'])} {status} it"))
        else:
            ev.append(EvidenceItem(source="check_budget", reference=c.call_id, finding=f"No budget record for department '{d.get('department')}'"))
    c = latest.get("search_catalog")
    if c and c.data is not None:
        matches = c.data.get("matches") or []
        if not matches:
            ev.append(EvidenceItem(source="search_catalog", reference=c.call_id, finding="No approved catalog product with the same vendor, product or category"))
        for m in matches[:3]:
            ev.append(EvidenceItem(source="search_catalog", reference=f"software_catalog.csv:{m['software_id']} ({c.call_id})",
                                   finding=f"Existing {m['product_name']} by {m['vendor_name']} ({', '.join(m['match_reasons'])}): status {m['status']}, "
                                           f"{m['licensed_seats']} seats, scope {m['scope']}, {_money(m['annual_cost_usd'])}/yr; {m['notes']}"))
    c = latest.get("get_purchase_history")
    if c and c.data:
        for p in (c.data.get("purchases") or [])[:2]:
            ev.append(EvidenceItem(source="get_purchase_history", reference=f"purchase_history.csv:{p['purchase_id']} ({c.call_id})",
                                   finding=f"{p['purchase_date']}: {p['department']} bought {p['product_name']} for {_money(p['annual_amount_usd'])}/yr ({p['status']}; {p['notes']})"))
        others = c.data.get("open_requests_same_department_vendor") or []
        if others:
            ev.append(EvidenceItem(source="get_purchase_history", reference=f"requests.json ({c.call_id})",
                                   finding="Other open requests from this department to this vendor: " + ", ".join(f"{o['request_id']} {_money(o['annual_cost_usd'])}" for o in others)))
    c = latest.get("get_vendor_registry")
    if c and c.data:
        d = c.data
        if d.get("found") and not d.get("ambiguous"):
            ev.append(EvidenceItem(source="get_vendor_registry", reference=f"vendors.csv:{d['vendor_id']} ({c.call_id})",
                                   finding=f"Registry: procurement {d['procurement_status']}, security {d['security_status']} "
                                           f"(review {d['security_review_date'] or 'none'}), legal terms {d['legal_terms_status']}; note: {d['notes']}"))
        else:
            ev.append(EvidenceItem(source="get_vendor_registry", reference=c.call_id, finding=f"Vendor '{c.args.get('vendor_name')}' not found (or ambiguous) in the vendor registry: treated as a new vendor"))
    c = latest.get("get_vendor_risk")
    if c:
        if c.ok and c.data and c.data.get("record_found"):
            d = c.data
            ev.append(EvidenceItem(source="get_vendor_risk", reference=f"{c.source} ({c.call_id})",
                                   finding=f"Vendor-risk service: review {d['security_review_status']} (last {d['last_review_date'] or 'none'}), "
                                           f"risk {d.get('risk_level', '?')}, personal data {d['processes_personal_data']}, "
                                           f"stores outside region {d['stores_data_outside_region']}; note: {d.get('notes', '')}"))
        elif c.ok:
            ev.append(EvidenceItem(source="get_vendor_risk", reference=f"{c.source} ({c.call_id})", finding="Vendor-risk service has no record for this vendor (no assessment on file)"))
        else:
            ev.append(EvidenceItem(source="get_vendor_risk", reference=f"{c.source} ({c.call_id})", finding=f"NOT VERIFIED: {c.error}"))
    for f in policy.findings:
        ev.append(EvidenceItem(source="evaluate_policy", reference=f["reference"], finding=f["finding"]))
    return ev


def next_step(policy: PolicyResult, questions: list[str]) -> str:
    approvers = ", ".join(policy.ordered_approvals) or "Procurement"
    if policy.action == "ESCALATE_MANUAL_REVIEW":
        return f"Procurement to review manually: {policy.critical_failure}. Do not route for approval until the requester is verified."
    if policy.action == "DO_NOT_PROCEED":
        return "Procurement to inform the requester that this vendor is blocked; any exception needs Procurement leadership sign-off."
    if policy.action == "REQUEST_CLARIFICATION":
        asks = policy.missing + [q for q in questions[:3] if q not in policy.missing]
        return ("Send the request back to the requester and ask for: " + "; ".join(asks) +
                ". Re-run the analysis once answered; no approval routing until then.")
    if policy.action == "VERIFY_EVIDENCE_FIRST":
        why = "; ".join(policy.flags.get("vendor_risk_unavailable", []) + policy.flags.get("conflicting_vendor_evidence", []))
        return f"Security to verify the vendor's status manually ({why}). Then send the evidence pack to: {approvers}."
    if policy.action == "REVIEW_EXISTING_TOOL_FIRST":
        return (f"Procurement to confirm with the requester whether the existing catalog tool covers the need "
                f"({policy.flags.get('existing_tool_overlap', [''])[0]}). If a real gap is confirmed, route to: {approvers}.")
    return f"Send the evidence pack to: {approvers}. Each approver decides; the copilot does not approve or purchase."


def run_copilot(request: dict, architecture: str = "single") -> tuple[ProcurementDecision, dict[str, Any]]:
    start = time.perf_counter()
    tb = ToolBox()
    stats = LLMStats()
    trace: dict[str, Any] = {"request_id": request.get("request_id"), "architecture": architecture, "errors": []}
    raw: dict[str, Any] = {}

    def policy_fn(args: dict) -> dict:
        p = evaluate(request, facts_from_calls(tb.calls), signals_from(args))
        rec = record(tb, "evaluate_policy", args, True, p.as_dict(), "src/copilot/policy.py")
        return {"call_id": rec.call_id, **p.as_dict()}

    llm = None
    if architecture in ("single", "staged"):
        try:
            llm = GeminiClient(stats)
        except LLMUnavailable as exc:
            trace["errors"].append(f"LLM unavailable: {exc}")
    try:
        if llm and architecture == "single":
            raw = agents.run_single(request, tb, llm, policy_fn)
        elif llm and architecture == "staged":
            raw = agents.run_staged(request, tb, llm, policy_fn)
    except LLMUnavailable as exc:
        trace["errors"].append(f"LLM failed mid-run: {exc}")
    except Exception as exc:  # never let the agent crash the product
        trace["errors"].append(f"agent error: {type(exc).__name__}: {exc}")
    trace["errors"] += raw.get("errors", [])

    # ---------------- guardrail
    defects = ensure_evidence(request, tb)
    if architecture != "single":
        defects = []  # staged/rules gather evidence in code, so there is no agent to grade
    final_raw = raw.get("final")
    signals = signals_from(final_raw)
    policy = evaluate(request, facts_from_calls(tb.calls), signals)
    if not any(c.tool == "evaluate_policy" for c in tb.calls):
        record(tb, "evaluate_policy", {"signals": "final"}, True, policy.as_dict(), "src/copilot/policy.py")
    llm_ok = final_raw is not None
    if architecture in ("single", "staged") and (not llm_ok or raw.get("errors")):
        policy.flag("llm_unavailable", "model output unavailable or incomplete; approvals and flags come from deterministic checks only"
                    + (f" ({raw.get('errors')[0][:120]})" if raw.get("errors") else ""))

    grounded, ungrounded = check_claims((final_raw or {}).get("evidence_claims"), tb)
    summary = str((final_raw or {}).get("summary") or "").strip()
    summary_rejected = None
    if summary and APPROVAL_CLAIM.search(summary):
        summary_rejected, summary = summary, ""
    if not summary:
        summary = "Deterministic checks completed; see evidence and required approvals."
    summary = summary[:700]

    overrides = []
    if final_raw:
        prop_action = final_raw.get("proposed_action")
        if prop_action and prop_action != policy.action:
            overrides.append({"field": "action", "llm": prop_action, "final": policy.action})
        prop_appr = set(final_raw.get("proposed_approvals") or [])
        if "proposed_approvals" in final_raw and prop_appr != set(policy.approvals):
            overrides.append({"field": "required_approvals", "llm": sorted(prop_appr), "final": policy.ordered_approvals})
        prop_flags = {str(f) for f in final_raw.get("proposed_risk_flags") or []}
        missing_flags = sorted(set(policy.flags) - prop_flags)
        if "proposed_risk_flags" in final_raw and missing_flags:
            overrides.append({"field": "risk_flags_added_by_code", "llm": sorted(prop_flags), "final": sorted(policy.flags)})
    if summary_rejected:
        overrides.append({"field": "summary", "llm": summary_rejected, "final": "replaced: summary claimed approval"})

    label = ACTION_LABELS[policy.action]
    if policy.action in ("ROUTE_FOR_APPROVAL", "VERIFY_EVIDENCE_FIRST") and policy.approvals:
        label += f" ({', '.join(policy.ordered_approvals)})"
    questions = [str(q)[:200] for q in (final_raw or {}).get("questions_for_requester") or []
                 if not APPROVAL_CLAIM.search(str(q))][:3]
    decision = ProcurementDecision(
        request_id=str(request.get("request_id")),
        recommendation=f"{label}. {summary}",
        evidence=build_evidence(tb, policy),
        required_approvals=policy.ordered_approvals,
        missing_information=policy.missing,
        risk_flags=list(policy.flags),
        next_step=next_step(policy, questions),
        human_review_required=True,
        telemetry=RunTelemetry(llm_calls=stats.calls, tool_calls=len(tb.calls), tool_names=tb.names),
    )
    trace.update({
        "action": policy.action, "policy": policy.as_dict(), "signals": signals.__dict__ | {"extra_data_classes": sorted(signals.extra_data_classes)},
        "llm_raw": raw, "llm_ok": llm_ok, "tool_calls": [c.as_dict() | {"latency_ms": round(c.latency_ms, 1)} for c in tb.calls],
        "agent_defects": defects, "overrides": overrides, "grounded_claims": grounded, "ungrounded_claims": ungrounded,
        "questions_for_requester": questions,
        "llm": {"calls": stats.calls, "cache_hits": stats.cache_hits, "latency_ms": round(stats.latency_ms, 1),
                "input_tokens": stats.input_tokens, "output_tokens": stats.output_tokens, "log": stats.log},
        "wall_ms": round((time.perf_counter() - start) * 1000, 1),
    })
    LAST_TRACES[str(request.get("request_id"))] = trace
    return decision, trace
