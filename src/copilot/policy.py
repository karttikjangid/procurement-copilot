"""Deterministic policy engine. Encodes data/procurement_policy.md section by section.

The LLM never decides approvals, thresholds, dates or flags. It may only pass
*signals* (extra sensitive data classes it noticed, an injection it noticed, an
unclear business purpose, whether an overlap has a credible gap). Signals can add
reviews; they can never remove one ("escalate, never de-escalate")."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from src.copilot.config import SECURITY_REVIEW_VALID_DAYS, reference_date
from src.copilot.injection import scan_texts
from src.copilot.tools import to_decimal

ROLE_ORDER = ["Manager", "Department Head", "Procurement", "Finance", "CFO", "Security", "Privacy", "Legal"]

SENSITIVE_CLASSES = {"source_code", "production", "confidential_documents", "employee_pii", "customer_pii", "credentials"}
PII_CLASSES = {"employee_pii", "customer_pii"}

# data_access_level -> sensitive classes. Unlisted values are treated as unknown (fail closed).
DATA_LEVEL_CLASSES: dict[str, set[str]] = {
    "none": set(), "public": set(), "internal": set(), "internal_documents": set(),
    "internal_marketing": set(), "internal_only": set(),
    "confidential_documents": {"confidential_documents"}, "confidential": {"confidential_documents"},
    "source_code": {"source_code"},
    "customer_pii": {"customer_pii"}, "customer_data": {"customer_pii"},
    "employee_pii": {"employee_pii"}, "employee_data": {"employee_pii"}, "hr_data": {"employee_pii"},
    "production_telemetry": {"production"}, "production": {"production"}, "production_access": {"production"},
    "cloud_account": {"production"},
    "credentials": {"credentials"}, "secrets": {"credentials"},
}
UNKNOWN_LEVELS = {"", "unknown", "tbd", "n/a", "na", "not sure", "unspecified", "null", "none provided"}

# Integration keywords -> sensitive classes (case-insensitive substring match).
INTEGRATION_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("git", "source code", "code repo", "bitbucket"), "source_code"),
    (("production", "cloud account", "aws", "gcp", "azure", "kubernetes", "prod "), "production"),
    (("document repository", "sharepoint", "google drive", "contract repository", "confidential"), "confidential_documents"),
    (("crm", "salesforce", "hubspot", "helpdesk", "zendesk", "ticket", "customer database"), "customer_pii"),
    (("hris", "workday", "payroll", "bamboohr", "employee records"), "employee_pii"),
    (("vault", "secret", "credential", "password", "api key"), "credentials"),
]

REQUIRED_FIELDS = [
    ("requester", "Requester and department could not be verified"),
    ("product_name", "Product name is missing"),
    ("vendor_name", "Vendor is missing"),
    ("annual_cost_usd", "Annual cost (or a reasonable annual estimate) is missing"),
    ("user_count", "Number of users/licenses is missing"),
    ("business_justification", "Business purpose is missing"),
    ("data_access_level", "Intended data access level is missing or unknown"),
    ("requested_integrations", "Required integrations are not stated"),
]

ACTION_LABELS = {
    "ESCALATE_MANUAL_REVIEW": "Escalate to manual review: the copilot could not complete a reliable analysis",
    "DO_NOT_PROCEED": "Do not proceed: vendor is blocked in the procurement registry",
    "REQUEST_CLARIFICATION": "Request clarification from the requester before any approval routing",
    "VERIFY_EVIDENCE_FIRST": "Route to manual review: vendor evidence is unavailable or conflicting",
    "REVIEW_EXISTING_TOOL_FIRST": "Review the existing approved tool before buying a new one",
    "ROUTE_FOR_APPROVAL": "Route to the required human approvers",
}


@dataclass
class Signals:
    """Optional judgment signals from an LLM. All fields default to 'no signal'."""
    extra_data_classes: set[str] = field(default_factory=set)
    injection_detected: bool = False
    injection_quote: str = ""
    business_purpose_clear: bool | None = None
    overlap_gap_credible: bool | None = None


@dataclass
class PolicyResult:
    approvals: dict[str, list[str]] = field(default_factory=dict)
    flags: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    findings: list[dict[str, str]] = field(default_factory=list)
    data_classes: set[str] = field(default_factory=set)
    data_level_unknown: bool = False
    vendor_assessment: dict[str, Any] = field(default_factory=dict)
    action: str = "ROUTE_FOR_APPROVAL"
    annual_cost: Decimal | None = None
    critical_failure: str | None = None

    def approve(self, role: str, reason: str) -> None:
        self.approvals.setdefault(role, [])
        if reason not in self.approvals[role]:
            self.approvals[role].append(reason)

    def flag(self, name: str, reason: str) -> None:
        self.flags.setdefault(name, [])
        if reason not in self.flags[name]:
            self.flags[name].append(reason)

    def note(self, finding: str, reference: str) -> None:
        self.findings.append({"finding": finding, "reference": reference})

    @property
    def ordered_approvals(self) -> list[str]:
        return [r for r in ROLE_ORDER if r in self.approvals] + sorted(r for r in self.approvals if r not in ROLE_ORDER)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "action_label": ACTION_LABELS[self.action],
            "approvals": {r: self.approvals[r] for r in self.ordered_approvals},
            "flags": self.flags, "missing_information": self.missing,
            "data_classes": sorted(self.data_classes), "data_level_unknown": self.data_level_unknown,
            "vendor_assessment": self.vendor_assessment,
            "annual_cost_usd": float(self.annual_cost) if self.annual_cost is not None else None,
            "findings": self.findings, "critical_failure": self.critical_failure,
        }


# ---------------------------------------------------------------- helpers

def threshold_approvals(amount: Decimal) -> tuple[list[str], str]:
    """Policy section 4. Tier edges follow the table exactly: 1,000.00 is tier 1,
    1,000.01 is tier 2, 10,000.00 is tier 2, 25,000.00 is tier 3."""
    if amount <= Decimal("1000"):
        return ["Manager"], "up to $1,000"
    if amount <= Decimal("10000"):
        return ["Department Head", "Procurement"], "$1,000.01 - $10,000"
    if amount <= Decimal("25000"):
        return ["Department Head", "Finance", "Procurement"], "$10,000.01 - $25,000"
    return ["Department Head", "Finance", "CFO", "Procurement"], "above $25,000"


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10]) if value else None
    except ValueError:
        return None


def review_state(status: Any, review_date: Any, ref: date) -> tuple[str, date | None, int | None]:
    """Normalise a security review into approved_current / expired / not_completed / missing / unknown."""
    s = str(status or "").strip().casefold()
    d = _parse_date(review_date)
    age = (ref - d).days if d else None
    if s in {"approved", "complete", "completed", "current", "passed"}:
        if d is None:
            return "missing", None, None
        if age is not None and age > SECURITY_REVIEW_VALID_DAYS:
            return "expired", d, age
        if age is not None and age < 0:
            return "unknown", d, age  # review dated after the reference date: cannot trust
        return "approved_current", d, age
    if s in {"expired", "lapsed", "stale"}:
        return "expired", d, age
    if s in {"pending", "not_completed", "not completed", "in_progress", "in progress", "under review", "draft"}:
        return "not_completed", d, age
    if s in {"rejected", "failed", "blocked"}:
        return "rejected", d, age
    return "unknown", d, age


def classify_data(request: dict) -> tuple[set[str], bool]:
    level_raw = request.get("data_access_level")
    level = str(level_raw).strip().casefold() if level_raw is not None else ""
    classes: set[str] = set()
    unknown = level in UNKNOWN_LEVELS or level_raw is None
    if not unknown:
        if level in DATA_LEVEL_CLASSES:
            classes |= DATA_LEVEL_CLASSES[level]
        else:
            # Unrecognised label: map by keyword, else fail closed as unknown.
            hit = False
            for key, mapped in DATA_LEVEL_CLASSES.items():
                if mapped and key in level:
                    classes |= mapped
                    hit = True
            if "pii" in level or "personal" in level:
                classes.add("customer_pii" if "customer" in level else "employee_pii")
                hit = True
            unknown = not hit and not any(k in level for k in ("internal", "public", "none"))
    for integration in request.get("requested_integrations") or []:
        text = f" {str(integration).casefold()} "
        for keywords, mapped in INTEGRATION_KEYWORDS:
            if any(k in text for k in keywords):
                classes.add(mapped)
    return classes, unknown


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().casefold() in UNKNOWN_LEVELS:
        return True
    return False


# ---------------------------------------------------------------- the engine

def evaluate(request: dict, facts: dict[str, Any], signals: Signals | None = None) -> PolicyResult:
    """facts: outputs of the deterministic tools, keyed by tool name, each the tool's
    `data` (or None if the tool failed). Missing keys mean 'not gathered'."""
    signals = signals or Signals()
    ref = reference_date()
    r = PolicyResult()

    requester = facts.get("lookup_requester")
    budget = facts.get("check_budget")
    catalog = facts.get("search_catalog")
    history = facts.get("get_purchase_history")
    registry = facts.get("get_vendor_registry")
    risk = facts.get("get_vendor_risk")
    risk_error = facts.get("get_vendor_risk__error")

    # ---- Section 1: required information
    if not requester or not requester.get("found"):
        r.missing.append(REQUIRED_FIELDS[0][1])
        r.critical_failure = "requester could not be verified in employees.csv"
        r.flag("requester_unverified", f"requester id '{request.get('requester_id')}' not found")
    for key, message in REQUIRED_FIELDS[1:]:
        value = request.get(key)
        if key == "requested_integrations":
            if value is None:  # an empty list means "no integrations", which is an answer
                r.missing.append(message)
            continue
        if key == "business_justification":
            if _is_missing(value) or len(str(value).strip()) < 8:
                r.missing.append(message)
            continue
        if _is_missing(value):
            r.missing.append(message)
    cost = to_decimal(request.get("annual_cost_usd"))
    if request.get("annual_cost_usd") is not None and (cost is None or cost < 0):
        r.missing.append("Annual cost is not a valid non-negative amount")
        cost = None
    users = to_decimal(request.get("user_count"))
    if request.get("user_count") is not None and (users is None or users <= 0):
        r.missing.append("Number of users/licenses is not a valid positive number")
    r.annual_cost = cost
    if signals.business_purpose_clear is False and "Business purpose is missing" not in r.missing:
        r.missing.append("Business purpose is unclear: state the concrete use case and who will use it")

    data_classes, level_unknown = classify_data(request)
    extra = {c for c in signals.extra_data_classes if c in SENSITIVE_CLASSES} - data_classes
    data_classes |= extra
    r.data_classes, r.data_level_unknown = data_classes, level_unknown
    if level_unknown and "Intended data access level is missing or unknown" not in r.missing:
        r.missing.append("Intended data access level is missing or unknown")
    if r.missing:
        r.flag("missing_information", "; ".join(r.missing))
    if extra:
        r.note(f"Request text/integrations indicate additional sensitive data: {', '.join(sorted(extra))}", "Policy 5/6 (LLM signal, escalate-only)")

    # ---- Section 4: financial thresholds (minimum business approvals)
    if cost is not None:
        roles, tier = threshold_approvals(cost)
        for role in roles:
            r.approve(role, f"annual amount ${cost:,.2f} is in tier '{tier}'")
        r.note(f"Annual amount ${cost:,.2f} falls in tier '{tier}': minimum approvals {', '.join(roles)}", "Policy 4")
    else:
        r.note("Financial approval tier cannot be determined until the annual cost is provided", "Policy 4")

    # ---- Section 2: budget
    if budget and budget.get("found"):
        if cost is not None:
            if budget["within_available_budget"] is False:
                r.flag("budget_insufficient", f"cost ${cost:,.0f} exceeds available ${budget['available_usd']:,.0f} (shortfall ${budget['shortfall_usd']:,.0f})")
                r.approve("Finance", "budget exception: cost exceeds available department budget")
    elif requester and requester.get("found"):
        r.flag("budget_unverified", f"no budget record for department '{requester.get('department')}'")
        r.approve("Finance", "department budget could not be verified")

    # ---- Section 3: existing software / overlap
    matches = (catalog or {}).get("matches") or []
    if matches:
        names = ", ".join(f"{m['product_name']} ({'/'.join(m['match_reasons'])}, {m['licensed_seats']} seats, {m['scope']})" for m in matches)
        r.flag("existing_tool_overlap", f"catalog already has: {names}")
        r.note(f"Existing catalog options to review first: {names}", "Policy 3")

    # ---- Vendor evidence (Sections 5, 7, 10)
    reg = registry if registry and registry.get("found") and not registry.get("ambiguous") else None
    if registry and registry.get("ambiguous"):
        r.missing.append("Vendor name matches several registry records; confirm which vendor")
        r.flag("missing_information", "ambiguous vendor name")
    reg_state, reg_date, reg_age = review_state(reg.get("security_status"), reg.get("security_review_date"), ref) if reg else ("missing", None, None)
    api_available = bool(risk) and not risk_error
    api_found = api_available and bool(risk.get("record_found"))
    if api_found:
        api_state, api_date, api_age = review_state(risk.get("security_review_status"), risk.get("last_review_date"), ref)
    else:
        api_state, api_date, api_age = ("unavailable" if not api_available else "missing"), None, None
    va = {"registry_state": reg_state, "registry_review_date": reg_date.isoformat() if reg_date else None,
          "registry_review_age_days": reg_age, "api_state": api_state,
          "api_review_date": api_date.isoformat() if api_date else None, "api_review_age_days": api_age,
          "reference_date": ref.isoformat(), "valid_days": SECURITY_REVIEW_VALID_DAYS}
    r.vendor_assessment = va

    if not api_available:
        r.flag("vendor_risk_unavailable", f"vendor-risk service could not be reached: {risk_error or 'not called'}")
        r.approve("Security", "vendor security status could not be verified (tool unavailable)")
        r.note("Vendor-risk status could NOT be verified; no favourable status was assumed", "Policy 10")

    conflict_reasons = []
    if reg and api_found:
        reg_label = str(reg.get("security_status", "")).casefold()
        api_label = str(risk.get("security_review_status", "")).casefold()
        reg_ok = reg_label in {"approved", "completed", "current"}
        api_ok = api_label in {"approved", "completed", "current"}
        if reg_ok != api_ok:
            conflict_reasons.append(f"registry says '{reg.get('security_status')}' but vendor-risk service says '{risk.get('security_review_status')}'")
        if reg_date and api_date and reg_date != api_date:
            conflict_reasons.append(f"review dates differ (registry {reg_date}, service {api_date})")
    if conflict_reasons:
        r.flag("conflicting_vendor_evidence", "; ".join(conflict_reasons))
        r.approve("Security", "registry and vendor-risk service disagree; manual verification required")

    states = {reg_state, api_state} - {"unavailable"}
    if "expired" in states:
        ages = [a for a in (reg_age, api_age) if a is not None]
        r.flag("vendor_review_expired", f"latest security review is {max(ages) if ages else '?'} days old (limit {SECURITY_REVIEW_VALID_DAYS}) as of {ref}")
        r.approve("Security", "vendor security assessment expired")
    if states & {"not_completed", "missing", "unknown", "rejected"}:
        r.approve("Security", "vendor security assessment missing or not completed")
    assessment_current = reg_state == "approved_current" and api_state == "approved_current" and not conflict_reasons
    r.note(
        f"Vendor security assessment: registry={reg_state}, service={api_state}; current={assessment_current} (reference date {ref}, valid {SECURITY_REVIEW_VALID_DAYS} days)",
        "Policy 5",
    )

    # ---- Section 5: use-case security triggers
    sensitive = data_classes & SENSITIVE_CLASSES
    if sensitive:
        r.approve("Security", f"request involves {', '.join(sorted(sensitive))}")
    if level_unknown:
        r.approve("Security", "data access level unknown: cannot rule out sensitive data (fail closed)")
    if "Security" in r.approvals:
        r.flag("security_review_required", "; ".join(r.approvals["Security"]))

    # ---- Section 6: privacy
    outside = bool(risk.get("stores_data_outside_region")) if api_found else None
    pii = data_classes & PII_CLASSES
    if pii:
        r.approve("Privacy", f"tool will process {', '.join(sorted(pii))}")
    if outside and (sensitive or level_unknown):
        r.approve("Privacy", "vendor may store sensitive data outside the operating region")
    if outside is None and (sensitive or level_unknown) and not api_found:
        r.approve("Privacy", "data residency could not be verified for sensitive data (fail closed)")
    if "Privacy" in r.approvals:
        r.flag("privacy_review_required", "; ".join(r.approvals["Privacy"]))

    # ---- Section 7: legal
    is_new = (reg is None) or str(reg.get("procurement_status", "")).casefold() in {"new", "pending", "onboarding", ""}
    if is_new and cost is not None and cost >= Decimal("10000"):
        r.approve("Legal", f"new vendor with annual spend ${cost:,.0f} (>= $10,000)")
    terms = str(reg.get("legal_terms_status", "")).strip() if reg else ""
    if terms.casefold() not in {"approved", "standard"}:
        r.approve("Legal", f"legal terms not approved (status: {terms or 'not on file'})")
    if outside and pii:
        r.approve("Legal", "cross-region processing of personal data")
    if is_new and cost is None:
        r.note("Vendor is new; Legal review will also be needed if annual spend is $10,000 or more", "Policy 7")
    if "Legal" in r.approvals:
        r.flag("legal_review_required", "; ".join(r.approvals["Legal"]))

    # ---- Section 8: AI tools (informational; data rules above already apply)
    if "ai" in str(request.get("category", "")).casefold().split() or any("ai" in str(m.get("category", "")).casefold().split() and "same_vendor" in m["match_reasons"] for m in matches):
        limited = [m for m in matches if "limited" in str(m.get("status", "")).casefold()]
        msg = "AI tool: an existing approval does not extend to new data classes or use cases"
        if limited:
            msg += f"; catalog entry '{limited[0]['product_name']}' is '{limited[0]['status']}' ({limited[0]['notes']})"
        r.note(msg, "Policy 8")

    # ---- Section 9: prompt injection (deterministic scan + LLM signal; never changes rules)
    texts = {"business_justification": request.get("business_justification"),
             "product_name": request.get("product_name"), "vendor_notes(registry)": reg.get("notes") if reg else None,
             "vendor_notes(service)": risk.get("notes") if api_found else None}
    texts.update({f"catalog_notes({m['software_id']})": m.get("notes") for m in matches})
    hits = scan_texts(texts)
    if signals.injection_detected:
        hits.append({"field": "llm_review", "snippet": signals.injection_quote[:160] or "(flagged by model)"})
    if hits:
        r.flag("prompt_injection_detected", "; ".join(f"{h['field']}: \"{h['snippet']}\"" for h in hits))
        r.note("Embedded instructions in business data were ignored; policy and evidence were applied as normal", "Policy 9")

    # ---- Split purchase (advisory, documented assumption; does not change approvals)
    others = (history or {}).get("open_requests_same_department_vendor") or []
    other_costs = [to_decimal(o.get("annual_cost_usd")) for o in others]
    if cost is not None and others and all(c is not None for c in other_costs):
        total = cost + sum(other_costs)  # type: ignore[arg-type]
        if threshold_approvals(total)[1] != threshold_approvals(cost)[1]:
            ids = ", ".join(o["request_id"] for o in others)
            r.flag("possible_split_purchase", f"open requests {ids} from the same department to the same vendor bring the total to ${total:,.0f}, a higher approval tier")

    # ---- Vendor blocked
    blocked = reg is not None and str(reg.get("procurement_status", "")).casefold() in {"blocked", "rejected", "terminated", "banned"}
    if blocked:
        r.flag("vendor_blocked", f"registry procurement status is '{reg.get('procurement_status')}'")

    # ---- Section 11: human authority + next action precedence (first match wins)
    if r.critical_failure:
        r.action = "ESCALATE_MANUAL_REVIEW"
    elif blocked:
        r.action = "DO_NOT_PROCEED"
    elif r.missing:
        r.action = "REQUEST_CLARIFICATION"
    elif "vendor_risk_unavailable" in r.flags or "conflicting_vendor_evidence" in r.flags:
        r.action = "VERIFY_EVIDENCE_FIRST"
    elif matches and signals.overlap_gap_credible is False:
        r.action = "REVIEW_EXISTING_TOOL_FIRST"
    else:
        r.action = "ROUTE_FOR_APPROVAL"
    return r


def facts_from_calls(calls: list) -> dict[str, Any]:
    """Build the facts dict from a ToolBox call log (latest successful call per tool)."""
    facts: dict[str, Any] = {}
    for c in calls:
        if c.tool == "evaluate_policy":
            continue
        if c.ok:
            facts[c.tool] = c.data
            facts.pop(f"{c.tool}__error", None)
        else:
            facts.setdefault(c.tool, None)
            facts[f"{c.tool}__error"] = c.error
    return facts
