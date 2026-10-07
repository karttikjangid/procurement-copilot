# Assumptions

1. **Reference date** is read from `data/procurement_policy.md` (2026-09-30), never the machine clock. Override with `REFERENCE_DATE`.
2. **Security review validity:** current when the review is at most 365 days old on the reference date (day 365 is current, day 366 expired). A review dated after the reference date is not trusted.
3. **Tier edges** follow the policy table literally: $1,000.00 is Manager; $1,000.01 to $10,000.00 is Department Head + Procurement; $25,000.01 adds CFO. Legal for new vendors starts at exactly $10,000.
4. **Budget:** cost equal to available budget is within budget. A missing or blank budget is "unverified" and adds Finance.
5. **Department Head:** a Director/VP in the requester's department other than the requester; otherwise the first Director/VP up the reporting line (Sales and Customer Success have no Director of their own in the data).
6. **Data sensitivity:** `data_access_level` is mapped to sensitive classes in code; unknown or unrecognised labels fail closed (Security review, and a clarification question). Integrations and LLM-detected text signals can add classes, never remove them.
7. **Privacy:** required for employee/customer PII, and when sensitive data may leave the operating region, including when residency cannot be verified (service down, or the field is null).
8. **Legal:** new vendor (registry status New, or not in the registry) with spend of $10,000 or more; legal terms not "Approved"; or cross-region processing of personal data.
9. **Vendor evidence:** registry and vendor-risk service are compared. Different approval labels or different review dates are a conflict; the copilot surfaces both values and routes to Security instead of choosing one. A 404 from the service means "no assessment on file", not an outage.
10. **Tool failure:** never treated as favourable. Unavailable vendor-risk data adds Security and the `vendor_risk_unavailable` flag.
11. **Overlap:** any catalog product with the same vendor, product or category is surfaced as `existing_tool_overlap`. Whether the request gives a credible gap is an LLM judgment; without one the action is "review the existing tool first".
12. **Empty integrations list** means "none"; only a missing field counts as missing information.
13. **Split purchases:** other open requests from the same department to the same vendor that together cross a higher tier raise an advisory `possible_split_purchase` flag. Approvals are not changed because the policy has no split-purchase rule.
14. **Every recommendation requires human review.** The copilot never approves, buys, edits budgets or accepts terms.
