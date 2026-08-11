# AOS Verification backend

AOS Verification is the existing account trust-review domain. The authoritative repository model supports exactly two request types: **Individual** and **Business**. It keeps one `AOS Verification Request` per account and reuses that row for resubmission after a rejected or revoked decision.

The authoritative states are:

`Pending -> Reviewing -> Approved | Rejected | Revoked`

The existing reviewer role is **System Manager** through Frappe Desk permissions. There is no separate public reviewer API. Review transitions are enforced again in the DocType controller so manipulated Desk/API saves cannot bypass server policy.

## Supported workflows

- Authenticated Individual submission with legal name, phone number, and one or more private verification documents.
- Authenticated Business submission with the repository's existing business fields plus private verification documents. A canonical `AOS Seller` must exist and Seller policy must permit verification submission.
- Owner-only status retrieval through `get_my_verification`.
- System Manager review through Desk: `Pending -> Reviewing/Approved/Rejected/Revoked`, `Reviewing -> Approved/Rejected/Revoked`, and `Approved -> Revoked`.
- Owner resubmission from `Rejected` or `Revoked`, reusing the same request row and replacing evidence atomically.
- Approval projection to `AOS Profile.is_verified`; Business approval also reuses the existing Seller projection policy.
- Canonical `verification_approved` and `verification_rejected` notifications through the transactional notification/outbox architecture.
- Account deletion revokes active/approved Verification state and releases raw private evidence for normal private-media cleanup while retaining the decision record.

## Intentionally unsupported

The repository does **not** model separate national-ID/passport/government-ID workflows, tax-document classes, proof-of-address classes, selfie/liveness checks, phone/email verification as part of this domain, verification levels, expiry/re-verification schedules, user cancel/withdraw, a dedicated Verification Reviewer role, a separate Seller-verification state machine, or provider-based KYC. These were not invented.

The repository also contains no malware/antivirus scanning service. Verification therefore inherits the hardened Media content validation (purpose, MIME/extension, size, image/document parsing and content checks) but does not claim malware scanning.

See `api.md`, `lifecycle.md`, `security.md`, `operations.md`, `migration.md`, and `testing.md`.
