# Architecture Decision Memo

## Decision

Ship **Architecture B (staged)**: code gathers all evidence, one analyst call returns judgment signals, the policy engine decides, one recommender call writes the summary. The shared code guardrail checks every output.

## Evidence

Same model (qwen3.5-9b, local), same cases, same labels, scored by code.

| Metric (9 matched cases, incl. all 6 public) | A: single agent | B: staged |
|---|---:|---:|
| Cases fully passing | 7/9 | 7/9 |
| Public minimum checks | 5/6 | 6/6 |
| Missed required review (under-escalation) | 0 | 0 |
| Extra reviewer added (over-escalation) | 0 | 1 |
| Avg LLM calls | 5.0 | 2.0 |
| Avg latency (local laptop) | 467 s | 149 s |
| Agent skipped a tool | 1 case | not possible |
| LLM evidence claims grounded | 95% | 97% |

On 28 cases, B against the no-LLM reference: missed reviews fell from 2 cases to 0, because the model noticed customer PII and production access stated only in the justification. Over-escalation rose from 0 to 2 cases.

## Trade-offs

A lets the model choose tools. It passed the same number of cases at 2.5 times the calls and 3 times the latency, and once skipped a required tool, which the guardrail had to re-run. B's evidence step is plain code, so it cannot skip a check, and its cost is fixed at two calls.

B's errors were all in the safe direction or recoverable: the analyst sometimes listed every data class (extra Security/Privacy review), and once judged a stated gap as not credible (BASE-02). A twice marked a clear purpose as unclear. No architecture missed a review the policy requires, because approvals come from code and the model can only add reviews.

The guardrail changed the model's proposed approvals or action in 33% (A) to 56% (B) of runs. Code authority is not optional.

## Risks and what to validate before production

- Small sample: A finished 9 cases and B 28, one repeat each, because local inference took about 1 to 12 minutes of model time per case. Differences of one case are within noise.
- A 9B local model. Re-run with a stronger hosted model and three repeats; track the over-escalation rate, since every false review costs an approver's time.
- Labels were written twice independently; two ambiguous cases (EXT-15, EXT-16) penalise B for asking a clarifying question that a reviewer might accept.

## Why this is the right MVP

The deterministic engine already passes 87% of all 39 cases. The model is needed only for judgment: hidden sensitivity, unclear purpose, and whether an existing tool covers the need. B gives the model exactly those questions and nothing else. Letting an agent plan tool calls cost 2.5 times the calls and one skipped check; each architecture got one case the other missed, so the extra autonomy buys nothing measurable.
