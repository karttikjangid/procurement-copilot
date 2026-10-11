# Evaluation summary

Cases: 13 (6 public + 4 other starter requests + 29 synthetic edge cases). Repeats: 1. Mode: live. Model: gemini-3.8-flash.
`rules` = deterministic checks only (no LLM), a reference row.

| Metric | rules |
|---|---:|
| Cases fully passing | 100% |
| Correct next action | 100% |
| Correct approvals | 100% |
| Correct risk flags | 100% |
| Missing-info correct | 100% |
| Public minimum checks (6 public cases) | - |
| Under-escalation (missed Finance/CFO/Security/Privacy/Legal) | 0% |
| Over-escalation (extra approvers) | 0% |
| Human review required on every case | 100% |
| Avg LLM calls | 0.00 |
| Avg tool calls | 7.00 |
| Avg latency, s (LLM time as recorded) | 0.1 |
| p95 LLM latency per case, s | 0.0 |
| Avg input / output tokens | 0 |
| Runs where the LLM failed (fell back to code) | 0% |
| Raw LLM action matched label (before guardrail) | 0% |
| Runs where guardrail overrode LLM action/approvals | 0% |
| Agent skipped a tool or used wrong arguments | 0% |
| LLM evidence claims grounded | 0% |

## Failures

None
