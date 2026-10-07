"""Extended evaluation: the same 39 labelled cases on every architecture.

  python evals/run_eval.py                         # rules + single + staged, 1 repeat, live (records responses)
  python evals/run_eval.py --repeats 2 --workers 2
  python evals/run_eval.py --replay                # rebuild results from recorded responses, no API key

Each case runs against its own copy of the data (with the case's patch applied)
and its own vendor-risk endpoint (with the case's injected fault), so cases can
run in parallel. Recorded LLM responses live in evals/llm_cache/.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src  # noqa: E402,F401  (loads .env)
from evals import harness_server  # noqa: E402
from evals.run_public_evals import evaluate as public_evaluate  # noqa: E402
from src.copilot.config import OVERRIDES  # noqa: E402
from src.copilot.pipeline import run_copilot  # noqa: E402

CASES = json.loads((ROOT / "evals/cases/cases.json").read_text())
LABELS = json.loads((ROOT / "evals/cases/labels.json").read_text())
PUBLIC = {c["request_id"]: c for c in json.loads((ROOT / "evals/public_cases.json").read_text())}
OUT = ROOT / "evals" / "results"
PORT = 8011
GATED = {"Finance", "CFO", "Security", "Privacy", "Legal"}  # reviews whose omission is a safety failure


def prepare(case: dict, tmp: Path) -> tuple[dict, Path]:
    d = tmp / case["case_id"]
    shutil.copytree(ROOT / "data", d)
    patch = case.get("data_patch") or {}
    for name, rows in patch.items():
        if name.endswith(".csv"):
            path = d / name
            with path.open(encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                fields, existing = reader.fieldnames, list(reader)
            key = fields[1] if name == "vendors.csv" else fields[0]
            keep = [r for r in existing if r[key] not in {x.get(key) for x in rows}]
            with path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(keep + [{k: ("" if v is None else v) for k, v in r.items()} for r in rows])
        elif name.endswith(".json"):
            path = d / name
            obj = json.loads(path.read_text())
            obj.update(rows)
            path.write_text(json.dumps(obj, indent=1))
    reqs = json.loads((d / "requests.json").read_text())
    if case.get("request"):
        reqs = [r for r in reqs if r["request_id"] != case["request"]["request_id"]] + [case["request"]]
        (d / "requests.json").write_text(json.dumps(reqs, indent=1))
        request = case["request"]
    else:
        request = next(r for r in reqs if r["request_id"] == case["request_id"])
    return request, d


def score(case: dict, decision, trace: dict) -> dict:
    lab = LABELS[case["case_id"]]
    appr, flags = set(decision.required_approvals), set(decision.risk_flags)
    checks = {}
    checks["action"] = trace["action"] in lab["actions"]
    if "approvals_exact" in lab:
        req, opt = set(lab["approvals_exact"]), set(lab.get("approvals_optional", []))
        checks["approvals"] = req <= appr <= req | opt
        expected_gated = req & GATED
        allowed = req | opt
    else:
        req = set(lab.get("approvals_include", []))
        checks["approvals"] = req <= appr
        expected_gated, allowed = req & GATED, None
    checks["flags"] = set(lab["flags_required"]) <= flags and not (set(lab["flags_forbidden"]) & flags)
    missing_text = " ".join(decision.missing_information).lower()
    checks["missing_info"] = all(t in missing_text for t in lab["missing_tokens"]) and \
        (lab.get("max_missing") is None or len(decision.missing_information) <= lab["max_missing"])
    ev_text = " ".join(e.finding for e in decision.evidence)
    checks["evidence"] = all(t in ev_text for t in lab["evidence_contains"]) and len(decision.evidence) >= 2
    checks["human_review"] = decision.human_review_required is True
    pub = PUBLIC.get(case.get("request_id", ""))
    public_failures = public_evaluate(decision, pub["expectations"]) if pub else []
    checks["public_minimum"] = not public_failures
    under = sorted(expected_gated - appr)
    over = sorted(appr - allowed) if allowed is not None else []
    raw = trace.get("llm_raw", {}).get("final") or {}
    return {
        **{f"ok_{k}": v for k, v in checks.items()}, "pass": all(checks.values()),
        "under_escalation": "|".join(under), "over_escalation": "|".join(over),
        "action": trace["action"], "expected_actions": "|".join(lab["actions"]),
        "approvals": "|".join(decision.required_approvals), "flags": "|".join(decision.risk_flags),
        "public_failures": " / ".join(public_failures),
        "llm_ok": trace.get("llm_ok"), "llm_errors": len(trace.get("errors", [])),
        "raw_action_ok": (raw.get("proposed_action") in lab["actions"]) if raw.get("proposed_action") else "",
        "overrides": len([o for o in trace.get("overrides", []) if o["field"] in ("action", "required_approvals")]),
        "agent_defects": len(trace.get("agent_defects", [])),
        "claims_grounded": len(trace.get("grounded_claims", [])), "claims_ungrounded": len(trace.get("ungrounded_claims", [])),
        "llm_injection_signal": trace.get("signals", {}).get("injection_detected"),
        "llm_extra_classes": "|".join(trace.get("signals", {}).get("extra_data_classes", [])),
        "llm_calls": decision.telemetry.llm_calls, "tool_calls": decision.telemetry.tool_calls,
        "wall_ms": trace["wall_ms"], "llm_ms": trace["llm"]["latency_ms"],
        "tokens_in": trace["llm"]["input_tokens"], "tokens_out": trace["llm"]["output_tokens"],
        "cache_hits": trace["llm"]["cache_hits"],
    }


def run_one(case: dict, arch: str, tmp: Path, repeat: int) -> dict:
    request, data_dir = prepare_cache[case["case_id"]]
    fault = case.get("vendor_fault") or "none"
    token = OVERRIDES.set({"DATA_DIR": str(data_dir), "VENDOR_RISK_BASE_URL": f"http://127.0.0.1:{PORT}/{case['case_id']}/{fault}"})
    try:
        start = time.perf_counter()
        decision, trace = run_copilot(request, arch)
        trace["wall_ms"] = round((time.perf_counter() - start) * 1000, 1)
    finally:
        OVERRIDES.reset(token)
    row = {"case_id": case["case_id"], "family": case["family"], "title": case["title"], "architecture": arch, "repeat": repeat, **score(case, decision, trace)}
    (OUT / "traces").mkdir(parents=True, exist_ok=True)
    (OUT / "traces" / f"{arch}_{case['case_id']}_r{repeat}.json").write_text(json.dumps({"decision": decision.model_dump(), "trace": trace}, indent=1, default=str))
    mark = "PASS" if row["pass"] else "FAIL"
    print(f"{mark} {arch:7} r{repeat} {case['case_id']:8} {row['action']:28} llm={row['llm_calls']} tools={row['tool_calls']} {row['wall_ms']/1000:.1f}s"
          + ("" if row["pass"] else f"  [{', '.join(k[3:] for k, v in row.items() if k.startswith('ok_') and not v)}]"), flush=True)
    return row


prepare_cache: dict[str, tuple[dict, Path]] = {}


def mean(xs):
    xs = [x for x in xs if x not in ("", None)]
    return statistics.mean(xs) if xs else 0


def summarise(rows: list[dict], archs: list[str]) -> str:
    lines = ["| Metric | " + " | ".join(archs) + " |", "|---|" + "---:|" * len(archs)]
    def per(fn, fmt="{:.0%}"):
        vals = []
        for a in archs:
            rs = [r for r in rows if r["architecture"] == a]
            vals.append(fmt.format(fn(rs)) if rs else "-")
        return vals
    n = lambda rs: len(rs)  # noqa: E731
    metrics = [
        ("Cases fully passing", lambda rs: sum(r["pass"] for r in rs) / n(rs)),
        ("Correct next action", lambda rs: sum(r["ok_action"] for r in rs) / n(rs)),
        ("Correct approvals", lambda rs: sum(r["ok_approvals"] for r in rs) / n(rs)),
        ("Correct risk flags", lambda rs: sum(r["ok_flags"] for r in rs) / n(rs)),
        ("Missing-info correct", lambda rs: sum(r["ok_missing_info"] for r in rs) / n(rs)),
        ("Public minimum checks (6 public cases)", lambda rs: sum(r["ok_public_minimum"] for r in rs if r["case_id"] in PUB_CASE_IDS) / max(1, sum(1 for r in rs if r["case_id"] in PUB_CASE_IDS))),
        ("Under-escalation (missed Finance/CFO/Security/Privacy/Legal)", lambda rs: sum(bool(r["under_escalation"]) for r in rs) / n(rs)),
        ("Over-escalation (extra approvers)", lambda rs: sum(bool(r["over_escalation"]) for r in rs) / n(rs)),
        ("Human review required on every case", lambda rs: sum(r["ok_human_review"] for r in rs) / n(rs)),
    ]
    for name, fn in metrics:
        lines.append(f"| {name} | " + " | ".join(per(fn)) + " |")
    num = [
        ("Avg LLM calls", lambda rs: mean([r["llm_calls"] for r in rs]), "{:.2f}"),
        ("Avg tool calls", lambda rs: mean([r["tool_calls"] for r in rs]), "{:.2f}"),
        ("Avg latency, s (LLM time as recorded)", lambda rs: mean([r["llm_ms"] for r in rs]) / 1000 + mean([max(0, r["wall_ms"] - r["llm_ms"]) for r in rs if not r["cache_hits"]] or [0]) / 1000, "{:.1f}"),
        ("p95 LLM latency per case, s", lambda rs: sorted(r["llm_ms"] for r in rs)[min(len(rs) - 1, -(-95 * len(rs) // 100) - 1)] / 1000, "{:.1f}"),
        ("Avg input / output tokens", lambda rs: mean([r["tokens_in"] for r in rs]), "{:.0f}"),
        ("Runs where the LLM failed (fell back to code)", lambda rs: sum(1 for r in rs if r["llm_errors"]) / n(rs), "{:.0%}"),
        ("Raw LLM action matched label (before guardrail)", lambda rs: sum(1 for r in rs if r["raw_action_ok"] is True) / max(1, sum(1 for r in rs if r["raw_action_ok"] != "")), "{:.0%}"),
        ("Runs where guardrail overrode LLM action/approvals", lambda rs: sum(1 for r in rs if r["overrides"]) / n(rs), "{:.0%}"),
        ("Agent skipped a tool or used wrong arguments", lambda rs: sum(1 for r in rs if r["agent_defects"]) / n(rs), "{:.0%}"),
        ("LLM evidence claims grounded", lambda rs: sum(r["claims_grounded"] for r in rs) / max(1, sum(r["claims_grounded"] + r["claims_ungrounded"] for r in rs)), "{:.0%}"),
    ]
    for name, fn, fmt in num:
        lines.append(f"| {name} | " + " | ".join(per(fn, fmt)) + " |")
    return "\n".join(lines)


PUB_CASE_IDS = {c["case_id"] for c in CASES if c.get("request_id") in PUBLIC}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archs", nargs="+", default=["rules", "single", "staged"])
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cases", nargs="*", help="case ids to run (default: all)")
    ap.add_argument("--replay", action="store_true", help="use recorded LLM responses only (no API key needed)")
    args = ap.parse_args()
    if args.replay:
        os.environ["LLM_MODE"] = "replay"
    os.environ.setdefault("LLM_CACHE", "on")
    cases = [c for c in CASES if not args.cases or c["case_id"] in args.cases]
    OUT.mkdir(parents=True, exist_ok=True)
    harness_server.start(PORT)
    tmp = Path(tempfile.mkdtemp(prefix="pc_eval_"))
    for c in cases:
        prepare_cache[c["case_id"]] = prepare(c, tmp)
        harness_server.CONTEXTS[c["case_id"]] = prepare_cache[c["case_id"]][1]
    rows: list[dict] = []
    for arch in args.archs:
        for rep in range(1, args.repeats + 1):
            os.environ["LLM_CACHE_SALT"] = f"rep{rep}"
            with ThreadPoolExecutor(max_workers=1 if arch == "rules" else args.workers) as pool:
                rows += list(pool.map(lambda c: run_one(c, arch, tmp, rep), cases))
    with (OUT / "per_case.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    archs = list(dict.fromkeys(r["architecture"] for r in rows))
    table = summarise(rows, archs)
    # consistency across repeats
    cons = []
    if args.repeats > 1:
        for a in archs:
            same = 0
            for c in cases:
                acts = {r["action"] + r["approvals"] for r in rows if r["architecture"] == a and r["case_id"] == c["case_id"]}
                same += len(acts) == 1
            cons.append(f"- {a}: {same}/{len(cases)} cases gave the same action and approvals on every repeat")
    failures = [f"- **{r['architecture']} r{r['repeat']} {r['case_id']}** ({r['title']}): failed "
                f"{', '.join(k[3:] for k, v in r.items() if k.startswith('ok_') and not v)}; got {r['action']} [{r['approvals']}] flags [{r['flags']}]"
                + (f"; under-escalated: {r['under_escalation']}" if r["under_escalation"] else "")
                + (f"; public: {r['public_failures']}" if r["public_failures"] else "")
                for r in rows if not r["pass"]]
    summary = (f"# Evaluation summary\n\nCases: {len(cases)} (6 public + 4 other starter requests + 29 synthetic edge cases). "
               f"Repeats: {args.repeats}. Mode: {'replay' if args.replay else 'live'}. Model: {os.getenv('MODEL_NAME', 'gemini-3.8-flash')}.\n"
               "`rules` = deterministic checks only (no LLM), a reference row.\n\n" + table + "\n\n" +
               ("## Consistency\n\n" + "\n".join(cons) + "\n\n" if cons else "") +
               "## Failures\n\n" + ("\n".join(failures) or "None") + "\n")
    (OUT / "summary.md").write_text(summary, encoding="utf-8")
    (OUT / "failures.md").write_text("# Failures\n\n" + ("\n".join(failures) or "None") + "\n", encoding="utf-8")
    print("\n" + table)
    print(f"\nWrote {OUT.relative_to(ROOT)}/summary.md, per_case.csv, failures.md, traces/")


if __name__ == "__main__":
    main()
