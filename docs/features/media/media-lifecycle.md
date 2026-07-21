# Media lifecycle

## States

- **Initialized** — metadata and private staging identity persisted; presigned PUT issued.
- **Uploaded** — staged bytes verified and promoted to final storage.
- **Processing** — asynchronous processing started for an unattached source.
- **Ready** — processing completed for an unattached source.
- **Attached** — owned by a compatible feature resource.
- **Failed** — verification or processing failed; reason/code recorded safely.
- **Orphaned** — detached without a replacement and eligible after retention.
- **Replaced** — detached because a confirmed replacement became authoritative.
- **Delete Pending** — deletion requested but not yet durably completed.
- **Deleted** — all known final/staging identities removed; metadata retained as tombstone.

## Direct upload sequence

1. Validate request against purpose policy.
2. Generate final key and private staging key.
3. Persist `Initialized` before issuing the URL.
4. Client PUTs bytes to the staged key.
5. Confirmation locks the Media row and validates expiry/object/content.
6. Copy staged bytes to the final bucket/key.
7. Persist verified metadata and `Uploaded`.
8. Delete current staging bytes and retain a cleanup marker until the original URL expires.

The retained staging identity is intentional: a presigned PUT cannot be revoked. If a client reuses the still-valid URL after confirmation, cleanup removes that late object after expiry.

## Idempotency

- A supplied idempotency key can reuse a still-valid initiated upload for the same user and purpose.
- Confirmation returns an already completed active record without copying again.
- Duplicate processing callbacks return the existing terminal job state.
- Repeated successful deletion returns the `Deleted` tombstone.

## Attachment and replacement

A feature validates the new Media object before modifying its resource. The service attaches the new object only after the resource exists and enforces purpose/resource/count rules. The old object is released only after the new relationship is confirmed. This prevents deleting the previous working image before its replacement is durable.

Released media clears attachment fields and becomes `Replaced` or `Orphaned`. It is not immediately hard-deleted; cleanup applies policy retention and verifies that no feature table still references it.

## Deletion failure

Deletion first records `Delete Pending`, then removes every known storage identity. A retryable storage failure increments bounded retry metadata and leaves `Delete Pending`. Cleanup retries later. Only successful storage deletion transitions to `Deleted`.

## Reconciliation

Scheduled cleanup handles expired initialized uploads, old unattached/failed/orphaned/replaced objects, delete-pending retries, and expired staging identities. It skips any object with attachment metadata or a real reference in known feature tables. An unknown reference-check failure is fail-safe and prevents deletion.
