# Test-set labelling and adjudication

Two labellers labelled all 39 cases independently from `data/procurement_policy.md` and the data files:

1. `evals/cases/labels_claude.json`: written together with the case inputs.
2. `evals/cases/labels_blind.json`: an independent agent that saw only the policy, the data and the case inputs (no implementation, no other labels).

| Measure | Agreement |
|---|---:|
| Next action (at least one acceptable action shared) | 39 / 39 |
| Required approvals | 37 / 39 |

## Disagreements and how they were settled

| Case | Claude | Blind | Decision in `labels.json` |
|---|---|---|---|
| BASE-06 (REQ-1006, no cost, data level "unknown") | Security required (unknown data, fail closed) | No approvals certain until cost is known | Not scored: approvals are provisional until clarification |
| BASE-09 (REQ-1009, vendor-risk API down, confidential documents) | Privacy required (residency cannot be verified) | Privacy not required | Privacy optional: present or absent both pass |

## Ambiguities the blind labeller raised (kept as documented assumptions)

- Extra seats on an existing product (BASE-01, BASE-03, EXT-28): route or review existing capacity first. Both actions accepted, because seat usage data does not exist.
- SignalWatch (BASE-07): registry label "Approved" vs service "expired" with the same date. Treated as a conflict (policy 5: "do not silently choose one").
- Vendor with no record (HTTP 404) is "no assessment on file", not an outage: Security review, no `vendor_risk_unavailable` flag.
- "board_minutes" (EXT-24): clarify, or read as confidential documents with Security. Both accepted.
- "approx 5k" (EXT-20): policy accepts a reasonable estimate, but the copilot asks the requester to confirm a number before tiering. Deterministic tiering needs a number.
- Requester is the department head (EXT-27, EXT-22): sign-off is routed to the next Director/VP up the reporting line.
- A test-design flaw was found: the first version of the synthetic vendor ("meeting notes") overlapped semantically with DocSpace. The synthetic product was changed to captioning before any evaluation run.
