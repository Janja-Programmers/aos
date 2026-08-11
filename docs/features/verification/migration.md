# Verification migration

`aos.patches.v1_0.harden_verification_subsystem` reloads the hardened Verification Request/Document schemas and adds only indexes justified by existing lookups and review queues:

- request `(user, status)`;
- review queue `(status, modified, name)`;
- reviewed lookup `(status, verified_on, name)`;
- document `media`.

The patch attempts unique constraints on one Verification Request per account and one evidence child row per Media ID **only when existing data is already clean**. If legacy duplicates are detected, it logs the condition and preserves all records for operator review. It does not delete, merge, or silently rewrite historical identity evidence.

The schema hardening also adds the previously referenced but missing `business_website` field, adds a hidden SHA-256 submission-idempotency field, bounds Data lengths, and disables web-search indexing/rename exposure for the sensitive Verification DocTypes.

The patch is registered after Accounts/Media hardening and contains no explicit commit.
