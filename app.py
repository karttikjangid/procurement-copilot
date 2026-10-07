"""Procurement Request Copilot UI. Run with: python run_local.py (or ./start.sh)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

import src  # noqa: F401  (loads .env)
from src.copilot import approvals, data
from src.copilot.config import llm_mode, model_name, reference_date
from src.copilot.pipeline import run_copilot
from src.copilot.policy import ACTION_LABELS

st.set_page_config(page_title="Procurement Request Copilot", layout="wide")
TONE = {"ROUTE_FOR_APPROVAL": "info", "VERIFY_EVIDENCE_FIRST": "warning", "REQUEST_CLARIFICATION": "warning",
        "REVIEW_EXISTING_TOOL_FIRST": "warning", "ESCALATE_MANUAL_REVIEW": "error", "DO_NOT_PROCEED": "error"}

st.title("Procurement Request Copilot")
st.caption(f"Recommends the next action with evidence. Humans make every approval. Policy reference date {reference_date()} · model {model_name()} · LLM mode {llm_mode()}")

# ---------------------------------------------------------------- sidebar
employees = {e["employee_id"]: e for e in data.employees()}
with st.sidebar:
    st.header("Request")
    source = st.radio("Source", ["Existing request", "New request"], horizontal=True)
    reqs = {r["request_id"]: r for r in data.requests()}
    if source == "Existing request":
        rid = st.selectbox("Request", list(reqs), format_func=lambda r: f"{r} · {reqs[r]['product_name']}")
        request = reqs[rid]
    else:
        with st.form("new"):
            emp = st.selectbox("Requester", list(employees), format_func=lambda e: f"{e} · {employees[e]['name']} ({employees[e]['department']})")
            product = st.text_input("Product", "")
            vendor = st.text_input("Vendor", "")
            category = st.text_input("Category", "")
            cost = st.text_input("Annual cost (USD)", "")
            users = st.text_input("Number of users", "")
            level = st.selectbox("Data access", ["none", "internal_documents", "internal_marketing", "confidential_documents", "source_code",
                                                 "customer_pii", "employee_pii", "production_telemetry", "credentials", "unknown"])
            integ = st.text_input("Integrations (comma separated, blank = none)", "")
            just = st.text_area("Business justification", "")
            submitted = st.form_submit_button("Use this request")
        request = {"request_id": "NEW-1", "requester_id": emp, "product_name": product or None, "vendor_name": vendor or None,
                   "category": category or None, "annual_cost_usd": cost or None, "user_count": users or None,
                   "business_justification": just or None, "data_access_level": level,
                   "requested_integrations": [x.strip() for x in integ.split(",") if x.strip()], "urgency": "normal"}
    arch = st.radio("Architecture", ["staged", "single", "rules"], horizontal=True,
                    help="staged = B (code gathers evidence, 2 agents) · single = A (one agent with tools) · rules = no LLM")
    acting = st.selectbox("Acting as (for approvals)", list(employees), format_func=lambda e: f"{employees[e]['name']} ({employees[e]['level']}, {employees[e]['department']})")

key = f"{request['request_id']}::{arch}"
left, right = st.columns([0.9, 1.1], gap="large")
with left:
    st.subheader("Request details")
    st.caption("Request text is untrusted business data; instructions inside it are ignored.")
    st.dataframe(pd.DataFrame([{"field": k, "value": json.dumps(v) if isinstance(v, list) else ("" if v is None else str(v))} for k, v in request.items()]),
                 hide_index=True, width="stretch")
    if st.button("Run analysis", type="primary", width="stretch"):
        with st.spinner("Gathering evidence and checking policy..."):
            decision, trace = run_copilot(request, arch)
        st.session_state[key] = (decision, trace)
        approvals.log(request["request_id"], "copilot", "-", "analysis_run", f"architecture={arch}",
                      {"action": trace["action"], "approvals": decision.required_approvals})

result = st.session_state.get(key)
with right:
    st.subheader("Recommendation")
    if not result:
        st.info("Run the analysis to see the recommendation.")
    else:
        decision, trace = result
        policy = trace["policy"]
        getattr(st, TONE.get(trace["action"], "info"))(f"**{ACTION_LABELS[trace['action']]}**")
        st.write(decision.recommendation.split(". ", 1)[1] if ". " in decision.recommendation else decision.recommendation)
        st.markdown("**Next step:** " + decision.next_step)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Approvals required (from policy engine)**")
            for role, reasons in policy["approvals"].items():
                st.markdown(f"- **{role}**: {'; '.join(reasons)}")
            if decision.missing_information:
                st.markdown("**Missing information**")
                for m in decision.missing_information:
                    st.markdown(f"- {m}")
        with c2:
            st.markdown("**Risk flags**")
            for flag, reasons in policy["flags"].items():
                st.markdown(f"- `{flag}`: {'; '.join(reasons)[:220]}")
        t = decision.telemetry
        st.caption(f"{t.llm_calls} LLM calls · {t.tool_calls} tool calls · {trace['wall_ms']/1000:.1f} s · human review required: {decision.human_review_required}")

if result:
    decision, trace = result
    st.divider()
    st.subheader("Evidence")
    st.dataframe(pd.DataFrame([e.model_dump() for e in decision.evidence]), hide_index=True, width="stretch")

    st.subheader("Human decisions")
    state, decided = approvals.status(request["request_id"], decision.required_approvals)
    st.markdown(f"Status: **{state}**")
    cols = st.columns(max(1, len(decision.required_approvals)))
    for col, role in zip(cols, decision.required_approvals):
        with col:
            st.markdown(f"**{role}**: {decided.get(role, 'pending')}")
            reason = st.text_input("Reason", key=f"reason-{key}-{role}")
            own = acting == request.get("requester_id")
            a, r = st.columns(2)
            if a.button("Approve", key=f"a-{key}-{role}", disabled=own or not reason):
                approvals.log(request["request_id"], acting, role, "approved", reason)
                st.rerun()
            if r.button("Reject", key=f"r-{key}-{role}", disabled=not reason):
                approvals.log(request["request_id"], acting, role, "rejected", reason)
                st.rerun()
            if own:
                st.caption("Requesters cannot approve their own request.")
    if trace["action"] in ("REQUEST_CLARIFICATION", "ESCALATE_MANUAL_REVIEW", "DO_NOT_PROCEED"):
        st.warning("Approvals should wait: the recommendation is to resolve the issues above first.")
    with st.expander("Audit log"):
        st.dataframe(pd.DataFrame(approvals.history(request["request_id"])), hide_index=True, width="stretch")

    with st.expander("Guardrail report: where code overrode or checked the LLM"):
        st.markdown("**Overrides**")
        st.json(trace["overrides"] or "none")
        st.markdown("**Agent defects** (tools skipped or called with wrong arguments; re-run by code)")
        st.json(trace["agent_defects"] or "none")
        st.markdown(f"**LLM evidence claims:** {len(trace['grounded_claims'])} grounded, {len(trace['ungrounded_claims'])} ungrounded (dropped)")
        if trace["ungrounded_claims"]:
            st.json(trace["ungrounded_claims"])
        st.markdown("**LLM judgment signals** (can only add reviews)")
        st.json(trace["signals"])
        if trace["errors"]:
            st.error("Errors: " + " | ".join(trace["errors"]))
    with st.expander("Trace: every tool call"):
        st.dataframe(pd.DataFrame([{k: (json.dumps(v, default=str)[:300] if isinstance(v, (dict, list)) else v)
                                    for k, v in c.items()} for c in trace["tool_calls"]]), hide_index=True, width="stretch")
        st.json(trace["llm"])

st.divider()
st.caption("Recommendations are advisory. The copilot cannot purchase, approve spend, change budgets or accept legal terms.")
