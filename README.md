# AI Procurement Request Copilot

An internal tool that reviews a software purchase request, gathers evidence with tools, applies the procurement policy in code, and recommends the next action. Humans make every approval.

Built for FDE Assessment 3 on top of the provided starter pack. The `handle_request(request_id, architecture)` adapter in `src/solution.py` is unchanged in signature.

**Design in one line:** the LLM interprets and explains; code decides approvals, thresholds, dates and flags; humans approve.

## Quick start

Requires Python 3.11+ (macOS ships 3.9: `brew install python@3.12`).

```bash
./start.sh                 # first run creates .venv, installs requirements, copies .env.example to .env
```

`start.sh` runs `run_local.py`, which starts the mock vendor-risk API on :8001 and the UI on :8501.

Model settings live in `.env` (see `.env.example`):

| Setting | Use |
|---|---|
| `GOOGLE_API_KEY` + `MODEL_NAME` | Gemini (default `gemini-3.7-flash`) |
| `LLM_PROVIDER=openai`, `LLM_BASE_URL`, `MODEL_NAME` | Any OpenAI-compatible local server (LM Studio, Ollama, vLLM) |
| `LLM_MODE=replay` | Recorded responses only, no key needed (recordings match the eval runs, so use it with `evals/run_eval.py --replay`; in the UI use `LLM_MODE=off` without a key) |
| `LLM_MODE=off` | Deterministic checks only |

Checks:

```bash
python verify_setup.py                                   # starter preflight
python -m pytest -q tests                                # 48 tests: 10 starter + 38 policy/regression (policy edges, fail-closed paths, review regressions)
python evals/run_public_evals.py --architecture staged   # needs the mock API running (python run_local.py)
python evals/run_eval.py --replay                        # full A/B evaluation from recorded responses, no key
```

## Product workflow

1. Pick a request (or enter a new one) and an architecture in the sidebar, then **Run analysis**.
2. The recommendation card shows the next action, the required approvers with the rule behind each, risk flags with reasons, missing information and the next step.
3. The evidence table lists every finding with its source file/record or API call and tool call id.
4. Approvers record approve/reject with a mandatory reason. The UI blocks the requester, anyone but the named manager or department head for those roles, one person acting in two roles, and any approval while the recommendation is clarification, escalation or a blocked vendor. Every action goes to the audit log.
5. The guardrail and trace panels show where code overrode the model, any tools the agent skipped, and every tool call.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the diagram, tools, agent roles, guardrail steps and stop conditions.

| | A: single agent | B: staged, 2 agents |
|---|---|---|
| Evidence gathering | The agent chooses tools (function calling) | Code calls all 6 evidence tools in a fixed order, then `evaluate_policy` |
| LLM calls | Variable (tool turns + final) | Always 2 (analyst, recommender) |
| Decision | Policy engine + shared guardrail | Policy engine + shared guardrail |

Tools: `lookup_requester`, `check_budget`, `search_catalog`, `get_purchase_history`, `get_vendor_registry`, `evaluate_policy` (all deterministic) and `get_vendor_risk` (external API with retries and fail-closed handling).

## Assumptions

See [docs/assumptions.md](docs/assumptions.md). The most important: reference date 2026-09-30 from the policy file; unknown data, unavailable tools and conflicting vendor records always add review, never remove it.

## Starter pack issues

The starter preflight passes, but 9 code issues and 10 data/policy issues were found and handled. See [docs/starter_issues.md](docs/starter_issues.md).

## Evaluation

- **Test set:** 39 cases = the 6 public cases, the 4 other starter requests, and 29 synthetic edge cases (threshold and date edges, budget edges, conflicting/unavailable/malformed vendor evidence, unknown and blocked vendors, unknown requester, prompt injection in request text and in API data, sensitive data visible only in the text, vague purpose, invalid fields, and combinations). Inputs: `evals/cases/cases.json`.
- **Labels:** written twice, independently. A blind labeller saw only the policy and data. Agreement was 39/39 on next action and 37/39 on approvals; disagreements were settled in `evals/adjudication.md`. Final labels: `evals/cases/labels.json`.
- **Scoring is done by code**, not an LLM judge: exact action, exact approval set, required and forbidden flags, missing-information items, evidence content, under- and over-escalation, plus the public runner's minimum checks.
- **Reference row:** `rules` runs the same pipeline with no LLM, to show what the model adds.
- **Faults are injected** per case by the eval's own vendor-risk service (timeout, malformed response, outage).
- **Outputs:** `evals/results/FINAL_RESULTS.md` (tables), `final_per_case.csv`, and one trace per run in `evals/results/traces/` (rules, A) and `evals/results_staged/traces/` (B). `run_eval.py` writes `summary.md` for whatever it just ran.
- **Reproducible:** every LLM response is recorded in `evals/llm_cache/`; `python evals/run_eval.py --replay` rebuilds `evals/results/` without a key.

### Results

Model: `qwen/qwen3.5-9b` in LM Studio on a MacBook Air (thinking off, temperature 0, one repeat). Free-tier Gemini allowed about 20 requests per model per day, too few for this comparison. Local inference took about 1 to 12 minutes of model time per case, so A finished 9 cases and B 28 before the deadline; A and B are compared only on cases both finished. Full tables and every failure: [evals/results/FINAL_RESULTS.md](evals/results/FINAL_RESULTS.md).

**A vs B on the 9 matched cases (the starter's REQ-1001 to REQ-1009, including all 6 public cases):**

| Metric | rules (no LLM) | A: single | B: staged |
|---|---:|---:|---:|
| Cases fully passing | 8/9 | 7/9 | 7/9 |
| Public minimum checks | 6/6 | 5/6 | 6/6 |
| Missed required review | 0 | 0 | 0 |
| Extra reviewer added | 0 | 0 | 1 |
| Avg LLM calls | 0 | 5.0 | 2.0 |
| Avg latency per case | 0.1 s | 467 s | 149 s |
| Agent skipped a tool (re-run by guardrail) | - | 1 | - |
| LLM evidence claims grounded in tool output | - | 95% | 97% |
| Guardrail overrode the model's approvals/action | - | 33% | 56% |

**B vs the deterministic reference on 28 cases:** missed required reviews fell from 2 cases (7%) to 0; extra reviewers rose from 0 to 2 cases (7%). The model caught customer PII and production access that appeared only in the justification text (EXT-15, EXT-16) and the TaskFlow overlap with no stated gap (BASE-08). The deterministic reference alone passes 34/39 (87%) of all cases and fails exactly the cases that need judgment.

**What went wrong (all in the safe direction):** B's analyst sometimes listed every sensitive data class (extra Security/Privacy review on BASE-07, EXT-04) and once judged a stated gap as not credible (BASE-02); A twice marked a clear purpose as unclear (BASE-01, BASE-07). No run missed a review the policy requires.

**Reproduce:** `python evals/summarize.py` rebuilds every table from the saved traces. `LLM_PROVIDER=openai MODEL_NAME=qwen/qwen3.5-9b python evals/run_eval.py --replay --archs staged --port 8012 --out results_replay` re-runs B from recorded responses with no model (A: `--archs single --port 8011`). The port is part of the recorded tool output, so it must match.

## Architecture decision

**Ship B (staged).** Same full-pass rate as A on the matched cases (7/9; B better on next action, A better on approvals and flags), 2.5 times fewer LLM calls, about 3 times lower latency, fixed cost per request, and evidence gathering that cannot skip a check because it is plain code. The model is only asked the judgment questions the rules cannot answer. Memo (under 500 words): [docs/decision_memo.md](docs/decision_memo.md).

## Known limitations

- Free-tier Gemini allowed about 20 requests per model per day, too few for a fair A/B run; the recorded evaluation used `qwen/qwen3.5-9b` locally.
- Department heads, Finance, Security, Privacy and Legal approvers are not modelled as people in the data, so the UI checks only Manager and Department Head identities.
- No seat-usage data: "unused existing capacity" cannot be verified.
- The keyword injection scanner is a backstop; disguised injections are caught only by the model. Neither path can change approvals.
- The summary filter is conservative and sometimes replaces a correct summary with a template sentence.
- Small sample: A finished 9 cases and B 28, one repeat each. Differences of one case are within noise.
- Two ambiguous cases (EXT-15, EXT-16) score B's clarification question as wrong; the blind labeller considered asking acceptable.
- The starter's `run_public_evals.py` needs a live model and the mock API; its checks are also applied inside `run_eval.py` ("Public minimum checks").
