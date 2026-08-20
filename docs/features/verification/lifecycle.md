# Verification lifecycle

## State machine

New requests are server-created as `Pending`; clients cannot set status.

Reviewer transitions, enforced for users with effective Write permission on `AOS Verification Request`, are:

- `Pending -> Reviewing`
- `Pending -> Approved`
- `Pending -> Rejected`
- `Pending -> Revoked`
- `Reviewing -> Approved`
- `Reviewing -> Rejected`
- `Reviewing -> Revoked`
- `Approved -> Revoked`

`Rejected` and `Revoked` do not reopen through arbitrary Desk edits. The authenticated submission service may resubmit either state back to `Pending` using the existing single-row model.

## Concurrency and integrity

Submission locks the account profile before locking its Verification request. This serializes the first insert as well as resubmission and prevents duplicate Pending requests under normal API races. A migration installs a unique account constraint when legacy data is already clean; legacy duplicates are preserved for operator review rather than destructively repaired.

Review transitions are conditional on the saved previous state and are checked in the DocType controller. Approval re-locks and revalidates the account at the decision boundary, preventing a stale Desk form from approving a deleted, disabled, suspended, or deactivated account. Submitted identity fields, ownership, evidence rows, and idempotency metadata are immutable during review; the canonical resubmission action is the only normal path that replaces them.

Approval/rejection/revocation timestamps and reviewer identity are server-stamped. Approval, profile projection, Seller projection (Business only), and transactional notification records participate in the same request transaction.

## Resubmission

Resubmission reuses the existing request row, clears prior decision metadata, replaces the child evidence set, releases evidence no longer used, and attaches the new private media within the caller transaction. Old released media becomes orphaned and is handled by the existing private-media cleanup policy.
