"""Build the final comparison from saved traces (no LLM calls).

  python evals/summarize.py

Reads evals/results/traces (rules, single) and evals/results_staged/traces (staged),
re-scores every trace against evals/cases/labels.json, and writes
evals/results/FINAL_RESULTS.md and evals/results/final_per_case.csv.
Comparisons are only made on cases that every compared architecture finished.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.run_eval import CASES, PUB_CASE_IDS, summarise, score  # noqa: E402
from src.contracts import ProcurementDecision  # noqa: E402

BY_ID = {c["case_id"]: c for c in CASES}
rows = []
for pattern in ("evals/results/traces/*_r1.json", "evals/results_staged/traces/*_r1.json"):
    for path in glob.glob(str(ROOT / pattern)):
        arch, cid = os.path.basename(path).split("_")[:2]
        if (arch, cid) in {(r["architecture"], r["case_id"]) for r in rows}:
            continue
        blob = json.loads(Path(path).read_text())
        decision = ProcurementDecision.model_validate(blob["decision"])
        rows.append({"case_id": cid, "family": BY_ID[cid]["family"], "title": BY_ID[cid]["title"],
                     "architecture": arch, "repeat": 1, **score(BY_ID[cid], decision, blob["trace"])})

def done(arch):
    return {r["case_id"] for r in rows if r["architecture"] == arch}

order = [c["case_id"] for c in CASES]
matched = sorted(done("rules") & done("single") & done("staged"), key=order.index)
b_set = sorted(done("rules") & done("staged"), key=order.index)
sections = []
sections.append(("A vs B on the matched set", matched, ["rules", "single", "staged"]))
sections.append(("B vs deterministic reference", b_set, ["rules", "staged"]))
sections.append(("Deterministic reference, all cases", sorted(done("rules"), key=order.index), ["rules"]))

out = ["# Final evaluation results", "",
       "Model: `qwen/qwen3.5-9b` in LM Studio on a MacBook Air (local), temperature 0, thinking off. One repeat.",
       "`rules` = the same pipeline with no LLM (reference row). Scores are computed by code against `evals/cases/labels.json`.",
       "Architecture A was stopped early because local inference took 4 to 20 minutes per case, so A vs B is compared only on the cases both finished.", ""]
for title, ids, archs in sections:
    sub = [r for r in rows if r["case_id"] in ids and r["architecture"] in archs]
    out += [f"## {title} ({len(ids)} cases)", "", "Cases: " + ", ".join(ids), "", summarise(sub, archs), ""]
fails = [r for r in rows if not r["pass"]]
out += ["## Every failure", ""]
for r in sorted(fails, key=lambda r: (r["architecture"], order.index(r["case_id"]))):
    bad = ", ".join(k[3:] for k, v in r.items() if k.startswith("ok_") and not v)
    out.append(f"- **{r['architecture']} {r['case_id']}** ({r['title']}): failed {bad}; got {r['action']} [{r['approvals']}]"
               + (f"; under-escalated {r['under_escalation']}" if r["under_escalation"] else "")
               + (f"; over-escalated {r['over_escalation']}" if r["over_escalation"] else ""))
(ROOT / "evals/results/FINAL_RESULTS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
with (ROOT / "evals/results/final_per_case.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: (r["architecture"], order.index(r["case_id"]))))
print("\n".join(out))
