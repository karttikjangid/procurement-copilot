# Evaluation summary

Cases: 39 (6 public + 4 other starter requests + 29 synthetic edge cases). Repeats: 1. Mode: live. Model: gemini-3.8-flash.
`rules` = deterministic checks only (no LLM), a reference row.

| Metric | rules |
|---|---:|
| Cases fully passing | 85% |
| Correct next action | 92% |
| Correct approvals | 95% |
| Correct risk flags | 90% |
| Missing-info correct | 97% |
| Public minimum checks (6 public cases) | 100% |
| Under-escalation (missed Finance/CFO/Security/Privacy/Legal) | 5% |
| Over-escalation (extra approvers) | 0% |
| Human review required on every case | 100% |
| Avg LLM calls | 0.00 |
| Avg tool calls | 7.00 |
| Avg latency, s (LLM time as recorded) | 0.3 |
| p95 LLM latency per case, s | 0.0 |
| Avg input / output tokens | 0 |
| Runs where the LLM failed (fell back to code) | 0% |
| Raw LLM action matched label (before guardrail) | 0% |
| Runs where guardrail overrode LLM action/approvals | 0% |
| Agent skipped a tool or used wrong arguments | 100% |
| LLM evidence claims grounded | 0% |

## Failures

- **rules r1 BASE-08** (Starter request REQ-1008): failed action; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags [existing_tool_overlap]
- **rules r1 EXT-14** (Justification claims security already signed off): failed flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement|Security|Privacy] flags [security_review_required|privacy_review_required]
- **rules r1 EXT-15** (Declared internal, text says customer PII): failed approvals, flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags []; under-escalated: Privacy|Security
- **rules r1 EXT-16** (Declared internal, text says production cloud access): failed approvals, flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags []; under-escalated: Security
- **rules r1 EXT-19** (Business purpose too vague to review): failed action, flags, missing_info; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags []
- **rules r1 EXT-29** (Task tracker overlap with injected approval claim): failed action; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags [existing_tool_overlap|prompt_injection_detected|possible_split_purchase]
