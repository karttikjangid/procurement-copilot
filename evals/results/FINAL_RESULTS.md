# Final evaluation results

Model: `qwen/qwen3.5-9b` in LM Studio on a MacBook Air (local), temperature 0, thinking off. One repeat.
`rules` = the same pipeline with no LLM (reference row). Scores are computed by code against `evals/cases/labels.json`.
Architecture A was stopped early because local inference took about 1 to 12 minutes of model time per case, so A vs B is compared only on the cases both finished.

## A vs B on the matched set (9 cases)

Cases: BASE-01, BASE-02, BASE-03, BASE-04, BASE-05, BASE-06, BASE-07, BASE-08, BASE-09

| Metric | rules | single | staged |
|---|---:|---:|---:|
| Cases fully passing | 89% | 78% | 78% |
| Correct next action | 89% | 78% | 89% |
| Correct approvals | 100% | 100% | 89% |
| Correct risk flags | 100% | 100% | 89% |
| Missing-info correct | 100% | 89% | 100% |
| Public minimum checks (6 public cases) | 100% | 83% | 100% |
| Under-escalation (missed Finance/CFO/Security/Privacy/Legal) | 0% | 0% | 0% |
| Over-escalation (extra approvers) | 0% | 0% | 11% |
| Human review required on every case | 100% | 100% | 100% |
| Avg LLM calls | 0.00 | 5.00 | 2.00 |
| Avg tool calls | 7.00 | 7.00 | 7.00 |
| Avg latency, s (LLM time as recorded) | 0.1 | 466.7 | 149.1 |
| p95 LLM latency per case, s | 0.0 | 696.1 | 248.0 |
| Avg input / output tokens | 0 | 0 | 696 |
| Runs where the LLM failed (fell back to code) | 0% | 0% | 11% |
| Raw LLM action matched label (before guardrail) | 0% | 78% | 89% |
| Runs where guardrail overrode LLM action/approvals | 0% | 33% | 56% |
| Agent skipped a tool or used wrong arguments | 0% | 11% | 0% |
| LLM evidence claims grounded | 0% | 95% | 97% |

## B vs deterministic reference (28 cases)

Cases: BASE-01, BASE-02, BASE-03, BASE-04, BASE-05, BASE-06, BASE-07, BASE-08, BASE-09, BASE-10, EXT-01, EXT-02, EXT-03, EXT-04, EXT-05, EXT-06, EXT-07, EXT-08, EXT-09, EXT-10, EXT-11, EXT-12, EXT-13, EXT-14, EXT-15, EXT-16, EXT-17, EXT-18

| Metric | rules | staged |
|---|---:|---:|
| Cases fully passing | 89% | 82% |
| Correct next action | 96% | 89% |
| Correct approvals | 93% | 93% |
| Correct risk flags | 93% | 96% |
| Missing-info correct | 100% | 100% |
| Public minimum checks (6 public cases) | 100% | 100% |
| Under-escalation (missed Finance/CFO/Security/Privacy/Legal) | 7% | 0% |
| Over-escalation (extra approvers) | 0% | 7% |
| Human review required on every case | 100% | 100% |
| Avg LLM calls | 0.00 | 2.00 |
| Avg tool calls | 7.00 | 7.00 |
| Avg latency, s (LLM time as recorded) | 0.4 | 126.2 |
| p95 LLM latency per case, s | 0.0 | 163.0 |
| Avg input / output tokens | 0 | 1768 |
| Runs where the LLM failed (fell back to code) | 0% | 4% |
| Raw LLM action matched label (before guardrail) | 0% | 89% |
| Runs where guardrail overrode LLM action/approvals | 0% | 43% |
| Agent skipped a tool or used wrong arguments | 0% | 0% |
| LLM evidence claims grounded | 0% | 94% |

## Deterministic reference, all cases (39 cases)

Cases: BASE-01, BASE-02, BASE-03, BASE-04, BASE-05, BASE-06, BASE-07, BASE-08, BASE-09, BASE-10, EXT-01, EXT-02, EXT-03, EXT-04, EXT-05, EXT-06, EXT-07, EXT-08, EXT-09, EXT-10, EXT-11, EXT-12, EXT-13, EXT-14, EXT-15, EXT-16, EXT-17, EXT-18, EXT-19, EXT-20, EXT-21, EXT-22, EXT-23, EXT-24, EXT-25, EXT-26, EXT-27, EXT-28, EXT-29

| Metric | rules |
|---|---:|
| Cases fully passing | 87% |
| Correct next action | 92% |
| Correct approvals | 95% |
| Correct risk flags | 92% |
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
| Agent skipped a tool or used wrong arguments | 0% |
| LLM evidence claims grounded | 0% |

## Every failure

- **rules BASE-08** (Starter request REQ-1008): failed action; got ROUTE_FOR_APPROVAL [Department Head|Procurement]
- **rules EXT-15** (Declared internal, text says customer PII): failed approvals, flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement]; under-escalated Privacy|Security
- **rules EXT-16** (Declared internal, text says production cloud access): failed approvals, flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement]; under-escalated Security
- **rules EXT-19** (Business purpose too vague to review): failed action, flags, missing_info; got ROUTE_FOR_APPROVAL [Department Head|Procurement]
- **rules EXT-29** (Task tracker overlap with injected approval claim): failed action; got ROUTE_FOR_APPROVAL [Department Head|Procurement]
- **single BASE-01** (Starter request REQ-1001): failed action, missing_info, public_minimum; got REQUEST_CLARIFICATION [Manager]
- **single BASE-07** (Starter request REQ-1007): failed action; got REQUEST_CLARIFICATION [Department Head|Procurement|Finance|Security]
- **staged BASE-02** (Starter request REQ-1002): failed action; got REVIEW_EXISTING_TOOL_FIRST [Department Head|Procurement|Finance|Security|Legal]
- **staged BASE-07** (Starter request REQ-1007): failed approvals, flags; got VERIFY_EVIDENCE_FIRST [Department Head|Procurement|Finance|Security|Privacy]; over-escalated Privacy
- **staged EXT-04** ($25,000.01 needs CFO): failed approvals; got ROUTE_FOR_APPROVAL [Department Head|Procurement|Finance|CFO|Security|Privacy]; over-escalated Privacy|Security
- **staged EXT-15** (Declared internal, text says customer PII): failed action; got REQUEST_CLARIFICATION [Department Head|Procurement|Security|Privacy]
- **staged EXT-16** (Declared internal, text says production cloud access): failed action; got REQUEST_CLARIFICATION [Department Head|Procurement|Security]
