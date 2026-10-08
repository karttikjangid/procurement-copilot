# Evaluation summary

Cases: 1 (6 public + 4 other starter requests + 29 synthetic edge cases). Repeats: 1. Mode: live. Model: qwen/qwen3.5-9b.
`rules` = deterministic checks only (no LLM), a reference row.

| Metric | staged |
|---|---:|
| Cases fully passing | 100% |
| Correct next action | 100% |
| Correct approvals | 100% |
| Correct risk flags | 100% |
| Missing-info correct | 100% |
| Public minimum checks (6 public cases) | 0% |
| Under-escalation (missed Finance/CFO/Security/Privacy/Legal) | 0% |
| Over-escalation (extra approvers) | 0% |
| Human review required on every case | 100% |
| Avg LLM calls | 2.00 |
| Avg tool calls | 7.00 |
| Avg latency, s (LLM time as recorded) | 66.3 |
| p95 LLM latency per case, s | 66.2 |
| Avg input / output tokens | 6265 |
| Runs where the LLM failed (fell back to code) | 0% |
| Raw LLM action matched label (before guardrail) | 100% |
| Runs where guardrail overrode LLM action/approvals | 0% |
| Agent skipped a tool or used wrong arguments | 0% |
| LLM evidence claims grounded | 86% |

## Failures

None
