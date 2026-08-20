# Verification operations

## Desk review

Reviewer authorization follows effective Write permission on `AOS Verification Request`. The source DocType JSON grants this to System Manager by default, and administrators may extend it to roles such as `AOS Moderator` through Role Permissions Manager. Reviewers use the existing Desk view. Server-side transition enforcement remains authoritative even if a request is crafted outside the Desk UI.

Reviewers should not copy raw identity evidence into comments/logs. Private evidence should be opened only through the existing authorized Media signed-URL path. Signed URLs must not be pasted into long-lived tickets or logs.

## Notifications

Approved and rejected decisions call the existing `NotificationService` helpers with the canonical categories `verification_approved` and `verification_rejected`. The notification/outbox write occurs in the same DB transaction, is deduped by Verification request ID plus the server-stamped review decision token, and external delivery failure does not require Verification state rollback.

## Account deletion and restore

Account deletion first clears the profile verified projection through the Accounts lifecycle, releases Verification evidence from the request, removes successfully released child evidence rows, and revokes Pending/Reviewing/Approved Verification requests. The decision record is retained. No MinIO network operation runs while deletion owns broad DB work; released objects are deleted by the existing Media orphan cleanup after the `verification_document` one-day retention window.

If an evidence release cannot be confirmed, its child relation is deliberately retained for an idempotent retry/repair rather than losing the only safe reference to a private object.

A restored account is not automatically re-verified. The retained request remains `Revoked` and the account must submit fresh evidence through the normal resubmission workflow.

## Expiry/revocation jobs

The repository does not model verification expiry timestamps or an expiry scheduler. `Revoked` exists as a decision state and is used by authorized review/account-deletion flows, but no new expiry/re-verification job was added.
