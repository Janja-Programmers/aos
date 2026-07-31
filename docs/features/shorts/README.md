# Shorts backend

Shorts uses Frappe for authorization, metadata, counters, transactions and the transactional outbox; MinIO for media; and the authenticated video-processing companion for FFmpeg work.

Canonical lifecycle:

`init_upload -> direct PUT -> confirm_upload -> processing -> ready`

The public boundary is `aos.api.v1.shorts`. Endpoint wrappers are thin and delegate to `aos.services.shorts`, while legacy implementation imports under `aos.api.shorts` remain compatible.

## Guarantees

- Public identifiers remain `SHORT-*`, `MEDIA-*`, `SOUND-*`, `ACC-*` and `CONV-*`.
- Unknown fields are rejected. Only Frappe's `cmd` transport field is ignored.
- Mutations use operation savepoints and never commit the caller's transaction.
- All visibility decisions use the canonical Social graph and enforce blocks in both directions.
- Processing callbacks are signed, generation-aware and replay-safe.
- Feed cursors are HMAC-signed, expiring and tamper-evident.
- All list/page sizes and maintenance operations are bounded.

See the adjacent documents for contracts, operations and deployment guidance.
