# Held-out check (hidden-style cases)

13 cases written after the main evaluation, with new vendors, requesters and values, to mimic the graders' hidden cases ("same interfaces, different values"). An AI agent wrote the cases and labelled them from `data/procurement_policy.md` only, without seeing the implementation or the main labels.

Run (deterministic engine, no model):

```bash
EVAL_CASE_DIR=evals/hidden LLM_MODE=off python evals/run_eval.py --archs rules --port 8031 --out hidden/results
```

Result: 13/13 fully passing, no missed or extra reviewers. Traces: `evals/hidden/results/traces/`.
