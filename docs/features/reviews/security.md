# Security

Key controls:

- Strict accepted-field allowlists and Frappe-owned `cmd` removal at the public v1 boundary.
- Server-derived reviewer, seller, communication evidence and verified-interaction badge.
- Composite deterministic review key with database uniqueness and safe duplicate reconciliation.
- Row locking for owner mutations, reactions, moderation callbacks and aggregate changes.
- Unicode normalisation; null/control, dangerous invisible/bidirectional control, HTML/script-scheme, excessive-link and repeated-character rejection; bounded plain text.
- Central Media ownership, purpose, readiness and lifecycle enforcement.
- Operation-specific application limits for reads, eligibility, creation, edits, deletion, voting and reports, in addition to edge baseline limits.
- Review reports reuse active central `AOS Report Reason` records; reporting never directly changes review visibility.
- Public errors are stable and internal exceptions are logged but not returned.
- Logs contain opaque IDs and bounded labels, never review bodies, reporter identity or PII.
- Moderation generation/correlation prevents stale callbacks from overwriting later edits.
- Public and self serializers never expose raw moderation/provider notes.

Residual risk: communication-based eligibility proves interaction but not a completed purchase. Moving to verified purchase requires a canonical completed-order/booking model and a versioned migration.
