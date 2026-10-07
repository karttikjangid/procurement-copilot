# Failures

- **rules r1 BASE-08** (Starter request REQ-1008): failed action; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags [existing_tool_overlap]
- **rules r1 EXT-14** (Justification claims security already signed off): failed flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement|Security|Privacy] flags [security_review_required|privacy_review_required]
- **rules r1 EXT-15** (Declared internal, text says customer PII): failed approvals, flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags []; under-escalated: Privacy|Security
- **rules r1 EXT-16** (Declared internal, text says production cloud access): failed approvals, flags; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags []; under-escalated: Security
- **rules r1 EXT-19** (Business purpose too vague to review): failed action, flags, missing_info; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags []
- **rules r1 EXT-29** (Task tracker overlap with injected approval claim): failed action; got ROUTE_FOR_APPROVAL [Department Head|Procurement] flags [existing_tool_overlap|prompt_injection_detected|possible_split_purchase]
