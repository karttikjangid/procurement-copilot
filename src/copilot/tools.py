"""Agent tools. Every tool is deterministic except get_vendor_risk, which calls the
external mock service. All tools return the same envelope so evidence can be
traced to a call id, and every call is counted for telemetry."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.parse import quote

import requests as http

from src.copilot import data
from src.copilot.config import reference_date, vendor_risk_base_url

DEPARTMENT_HEAD_LEVELS = ("Director", "VP", "SVP", "Head", "C-level")


@dataclass
class ToolCall:
    call_id: str
    tool: str
    args: dict[str, Any]
    ok: bool
    data: Any = None
    error: str | None = None
    source: str = ""
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id, "tool": self.tool, "args": self.args, "ok": self.ok,
            "data": self.data, "error": self.error, "source": self.source,
        }


def to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).replace(",", "").replace("$", "").strip()
        if not text:
            return None
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


@dataclass
class ToolBox:
    """Holds the call log for one run. Agents call tools through `call`."""
    calls: list[ToolCall] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [c.tool for c in self.calls]

    def call(self, tool: str, **args: Any) -> ToolCall:
        fn: Callable[..., tuple[bool, Any, str | None, str]] = TOOL_FUNCTIONS[tool]
        start = time.perf_counter()
        try:
            ok, payload, error, source = fn(**args)
        except TypeError as exc:  # bad arguments from an agent
            ok, payload, error, source = False, None, f"bad arguments: {exc}", tool
        except Exception as exc:  # a tool must never crash the run
            ok, payload, error, source = False, None, f"{type(exc).__name__}: {exc}", tool
        result = ToolCall(
            call_id=f"T{len(self.calls) + 1}", tool=tool, args=args, ok=ok, data=payload,
            error=error, source=source, latency_ms=(time.perf_counter() - start) * 1000,
        )
        self.calls.append(result)
        return result

    def latest(self, tool: str) -> ToolCall | None:
        for c in reversed(self.calls):
            if c.tool == tool:
                return c
        return None


# ---------------------------------------------------------------- deterministic tools

def _employee_index() -> dict[str, dict[str, str]]:
    return {e["employee_id"]: e for e in data.employees()}


def _chain(emp_id: str, index: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    chain, seen = [], set()
    current = index.get(emp_id)
    while current and current.get("manager_id") and current["manager_id"] not in seen:
        seen.add(current["manager_id"])
        current = index.get(current["manager_id"])
        if current:
            chain.append(current)
    return chain


def lookup_requester(employee_id: str):
    index = _employee_index()
    emp = index.get(str(employee_id or "").strip())
    if emp is None:
        return True, {"found": False, "employee_id": employee_id}, None, "employees.csv"
    chain = _chain(emp["employee_id"], index)
    manager = chain[0] if chain else None
    # Department head: a Director/VP in the same department other than the requester,
    # else the first Director/VP up the reporting line (documented assumption).
    same_dept_heads = [e for e in index.values() if e["department"] == emp["department"]
                       and e["level"] in DEPARTMENT_HEAD_LEVELS and e["employee_id"] != emp["employee_id"]]
    head, head_basis = None, ""
    if same_dept_heads:
        head, head_basis = same_dept_heads[0], "Director/VP in the same department"
    else:
        for person in chain:
            if person["level"] in DEPARTMENT_HEAD_LEVELS:
                head, head_basis = person, "first Director/VP in the reporting line (no head listed in the department)"
                break
    return True, {
        "found": True,
        "employee_id": emp["employee_id"], "name": emp["name"], "department": emp["department"],
        "level": emp["level"], "country": emp["country"],
        "manager": {"employee_id": manager["employee_id"], "name": manager["name"], "level": manager["level"]} if manager else None,
        "department_head": {"employee_id": head["employee_id"], "name": head["name"], "department": head["department"], "basis": head_basis} if head else None,
    }, None, "employees.csv"


def check_budget(department: str, annual_cost_usd: Any = None):
    row = next((b for b in data.budgets() if b["department"].casefold() == str(department or "").strip().casefold()), None)
    if row is None:
        return True, {"found": False, "department": department}, None, "department_budgets.csv"
    available = to_decimal(row["available_usd"])
    cost = to_decimal(annual_cost_usd)
    within = None if cost is None or available is None else cost <= available
    return True, {
        "found": True, "department": row["department"],
        "annual_software_budget_usd": int(to_decimal(row["annual_software_budget_usd"]) or 0),
        "committed_usd": int(to_decimal(row["committed_usd"]) or 0),
        "available_usd": float(available) if available is not None else None,
        "available_valid": available is not None,
        "requested_annual_cost_usd": float(cost) if cost is not None else None,
        "within_available_budget": within,
        "shortfall_usd": float(cost - available) if within is False else 0.0,
    }, None, "department_budgets.csv"


def search_catalog(product_name: str = "", vendor_name: str = "", category: str = ""):
    p, v, c = data.norm_name(product_name), data.norm_name(vendor_name), data.norm_name(category)
    matches = []
    for item in data.catalog():
        reasons = []
        ip, iv, ic = data.norm_name(item["product_name"]), data.norm_name(item["vendor_name"]), data.norm_name(item["category"])
        if v and iv == v:
            reasons.append("same_vendor")
        if p and (ip == p or (len(ip) > 3 and ip in p) or (len(p) > 3 and p in ip)):
            reasons.append("same_product")
        if c and ic == c:
            reasons.append("same_category")
        if reasons:
            matches.append({**item, "match_reasons": reasons})
    return True, {"query": {"product_name": product_name, "vendor_name": vendor_name, "category": category},
                  "matches": matches}, None, "software_catalog.csv"


def get_purchase_history(department: str = "", vendor_name: str = "", request_id: str = ""):
    v, d = data.norm_name(vendor_name), str(department or "").casefold()
    ref = reference_date()
    rows = []
    for row in data.purchase_history():
        if v and data.norm_name(row["vendor_name"]) == v:
            rows.append({**row, "match": "same_vendor"})
    # Other open requests from the same department to the same vendor (split-purchase check).
    index = _employee_index()
    open_requests = []
    for req in data.requests():
        if str(req.get("request_id")) == str(request_id):
            continue
        emp = index.get(str(req.get("requester_id")))
        if emp and emp["department"].casefold() == d and v and data.norm_name(req.get("vendor_name")) == v:
            open_requests.append({k: req.get(k) for k in ("request_id", "requester_id", "product_name", "annual_cost_usd")})
    return True, {"reference_date": ref.isoformat(), "purchases": rows, "open_requests_same_department_vendor": open_requests}, None, "purchase_history.csv + requests.json"


def get_vendor_registry(vendor_name: str):
    v = data.norm_name(vendor_name)
    hits = [r for r in data.vendors() if data.norm_name(r["vendor_name"]) == v]
    if not hits:
        return True, {"found": False, "vendor_name": vendor_name}, None, "vendors.csv"
    if len(hits) > 1:
        return True, {"found": True, "ambiguous": True, "records": hits}, None, "vendors.csv"
    return True, {"found": True, "ambiguous": False, **hits[0]}, None, "vendors.csv"


# ---------------------------------------------------------------- external tool

VENDOR_RISK_FIELDS = {"security_review_status", "last_review_date", "processes_personal_data", "stores_data_outside_region"}


def get_vendor_risk(vendor_name: str, retries: int = 2, timeout_seconds: float = 3.0):
    """Mock vendor-risk API with retry. 404 = no record (valid answer), 5xx/timeout/
    malformed JSON = unavailable. Never returns a favourable default."""
    # Resolve spelling variants ("signflow inc.") to the registry's canonical name first.
    canonical = next((r["vendor_name"] for r in data.vendors() if data.norm_name(r["vendor_name"]) == data.norm_name(vendor_name)), vendor_name)
    url = f"{vendor_risk_base_url()}/vendor-risk/{quote(str(canonical or ''), safe='')}"
    last_error = "unknown error"
    for attempt in range(retries + 1):
        try:
            response = http.get(url, timeout=timeout_seconds)
            if response.status_code == 404:
                return True, {"record_found": False, "vendor_name": vendor_name}, None, f"GET {url}"
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            elif response.status_code >= 400:
                return False, None, f"HTTP {response.status_code}: {response.text[:200]}", f"GET {url}"
            else:
                body = response.json()
                if not isinstance(body, dict) or not VENDOR_RISK_FIELDS.issubset(body):
                    missing = sorted(VENDOR_RISK_FIELDS - set(body if isinstance(body, dict) else {}))
                    return False, body, f"malformed response, missing fields: {missing}", f"GET {url}"
                return True, {"record_found": True, **body}, None, f"GET {url}"
        except http.Timeout:
            last_error = f"timeout after {timeout_seconds}s"
        except http.ConnectionError as exc:
            last_error = f"connection error: {type(exc).__name__}"
        except ValueError:
            last_error = "response was not valid JSON"
        if attempt < retries:
            time.sleep(0.2 * (2 ** attempt))
    return False, None, f"vendor-risk service unavailable after {retries + 1} attempts ({last_error})", f"GET {url}"


TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "lookup_requester": lookup_requester,
    "check_budget": check_budget,
    "search_catalog": search_catalog,
    "get_purchase_history": get_purchase_history,
    "get_vendor_registry": get_vendor_registry,
    "get_vendor_risk": get_vendor_risk,
}
# evaluate_policy is registered in policy.py (it needs the other tools' results).

DETERMINISTIC_TOOLS = {"lookup_requester", "check_budget", "search_catalog", "get_purchase_history",
                       "get_vendor_registry", "evaluate_policy"}


def record(tb: ToolBox, tool: str, args: dict[str, Any], ok: bool, payload: Any, source: str) -> ToolCall:
    """Log a tool call that was executed outside ToolBox.call (evaluate_policy)."""
    result = ToolCall(call_id=f"T{len(tb.calls) + 1}", tool=tool, args=args, ok=ok, data=payload, source=source)
    tb.calls.append(result)
    return result
