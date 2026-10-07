# Starter pack issues found and how they were handled

Line numbers refer to the starter pack as provided (commit `3c95289`). `verify_setup.py` and the starter tests passed as shipped; the issues below are logic, data and evaluation problems that the preflight does not catch.

## Code

| # | File:line (starter) | Issue | Effect | Fix |
|---|---|---|---|---|
| 1 | `mock_api/app.py:22` | `unquote(vendor_name)` decodes a path parameter that FastAPI has already decoded | A vendor name containing `%` is looked up under the wrong key | Removed the second decode |
| 2 | `mock_api/app.py:10` | Vendor-risk data loaded once at import | Swapping `data/` (hidden cases) needs a server restart or silently serves stale data | Data read per request from `DATA_DIR` |
| 3 | `src/vendor_client.py:13` | `raise_for_status()` turns 404 (no assessment on file) and 503 (outage) into the same exception; no retry | A tool built on it cannot tell "unknown vendor" from "service down" | New tool `get_vendor_risk` (src/copilot/tools.py): 404 is a valid "no record" answer; 5xx, 429, timeouts and malformed JSON retry twice, then report unavailable; never a favourable default |
| 4 | `src/vendor_client.py:10` | Vendor name sent exactly as typed | "signflow inc." gets a 404 instead of SignFlow's record | Name resolved to the registry's canonical spelling first |
| 5 | `.gitignore:7` | `evals/results_*.csv` ignored | Evaluation results (a required deliverable) would never reach the repo | Re-included |
| 6 | `run_local.py:48-62` | Port 8001 hard-coded while clients read `VENDOR_RISK_BASE_URL` | Changing the URL in `.env` breaks the app silently | Left as is (documented); keep the default URL |
| 7 | `evals/run_public_evals.py:26` | Expectations matched by substring (`"security"`, `"data"`, `"user"`) | A loose answer such as any flag containing "security" passes | Kept for the public runner; the extended eval scores exact approval sets and named flags |
| 8 | `verify_setup.py:33` | Hard fail below Python 3.11 while the README says "recommended" | Confusing on machines with 3.10 | Not changed; README states 3.11+ is required |
| 9 | `src/data_access.py` | pandas turns empty CSV cells into `NaN` | `NaN` dates/statuses compare as truthy strings in naive code | New loaders (src/copilot/data.py) read CSV as clean strings |

## Data

| # | Where | Observation | Handling |
|---|---|---|---|
| 10 | `employees.csv` | Sales (E003) and Customer Success (E005) report to E007, whose department is "Go To Market"; no Director exists inside Sales or Customer Success, and there is no Go To Market budget row | Department Head = first Director/VP up the reporting line, stated in the evidence |
| 11 | `vendors.csv` V005 SignalWatch | Registry says security "Approved", but the review date 2025-07-01 is 456 days before the reference date and the vendor-risk service says "expired" | Surfaced as `conflicting_vendor_evidence` + `vendor_review_expired`, routed to Security (policy 5) |
| 12 | `requests.json` REQ-1006 | `data_access_level` is the string "unknown", not null | Treated as missing; Security added (fail closed) |
| 13 | `requests.json` | `requested_integrations: []` is ambiguous (none vs not provided) | `[]` = "no integrations"; only `null` counts as missing (PUB-01 requires zero missing items) |
| 14 | `requests.json` REQ-1010 | "One-time annual training package" | Treated as an annual amount of $950 |
| 15 | `requests.json` REQ-1001 + REQ-1010 | Same department, same vendor, $800 + $950 = $1,750 crosses the $1,000 Manager tier | Advisory flag `possible_split_purchase`; approvals unchanged (policy has no split-purchase rule) |
| 16 | `procurement_policy.md` s.4 vs s.7 | Tier edge is "$10,000.01" while Legal triggers at "$10,000 or more" | Both encoded exactly; boundary unit tests on both sides |
| 17 | `procurement_policy.md` s.6 | "Operating region" is not defined | Relies on the service's `stores_data_outside_region` field |
| 18 | `software_catalog.csv` | No seat-usage data | "Unused existing capacity" cannot be verified; expansions accept route or review-existing |
| 19 | `data/README.md`, policy | No defined taxonomy for `data_access_level` | Explicit mapping in src/copilot/policy.py; unrecognised labels fail closed as unknown |
