# AOS Verification

This directory is the canonical documentation location for the Verification backend. `README.md` owns the domain contract and `api.md` exists only because the repository-wide API inventory generator owns that code-derived endpoint table.

## Feature overview

Verification owns the lifecycle for an account submitting private identity/business evidence and an authorized reviewer deciding whether that account is verified. It owns the verification request, its sensitive evidence references, the review state machine, the Accounts verified projection, and the decision notification intent.

Verification does **not** own authentication, account identity, binary storage, push/email delivery, country/language/currency reference data, seller lifecycle, Catalog data, phone/email authentication challenges, KYC-provider integrations, malware scanning, or a second user identity model.

The current product supports two request types only:

- `Individual`
- `Business`

There is exactly one `AOS Verification Request` per account. Rejected or revoked requests are resubmitted by replacing the submission/evidence on that same row. This bounds account lookups, prevents parallel “current” records, and preserves the review record identity.

## Production-ready dependencies

### Authentication

All public Verification APIs derive identity from the canonical authenticated Frappe/AOS session through the shared auth boundary. Verification accepts no client `user`, email identity alias, password, session token, API secret, or custom auth token.

### Accounts

`AOS Profile` is the canonical account identity/lifecycle row. Submission locks the Accounts profile before touching the Verification row so concurrent submission, account deletion, and approval follow a consistent lock order. Public account identity uses the opaque `ACC-*` profile identity.

`AOS Profile.is_verified` is a read-optimized Accounts projection. Its lifecycle is not independently client-controlled: Verification approval sets it, Verification revocation clears it, and Accounts deletion lifecycle remains authoritative for deleted-account visibility/retention behavior. `get_my_verification` derives Verification truth from the Verification request state rather than trusting that projection as a second source of truth.

### Media

Evidence uses the canonical Media purpose `verification_document`. Verification stores only Media IDs and requires Media to enforce ownership, private visibility, readiness, attachment lifecycle, signed access, deletion/orphan policy, and storage-provider details.

Clients cannot submit bucket names, object keys, filesystem paths, provider names, credentials, direct storage URLs, legacy file URLs, or arbitrary `File` references.

### Notifications

Approved and rejected decisions use the canonical Notifications service. Verification writes only the canonical notification intent; Notifications owns persistence, dedupe, durable delivery jobs/outbox, realtime publication, provider delivery and retries.

The notification intent participates safely in the decision transaction using Notifications' savepoint isolation. External delivery/realtime occurs after commit through the production Notifications/outbox architecture, so provider failure cannot roll back an already accepted Verification decision.

### Localization

Verification currently stores no country/language/currency master data. If future product requirements need such reference data, the Localization domain must remain authoritative.

### Catalog

Verification currently has no genuine Catalog dependency. Business category is submission text, not a duplicated Catalog taxonomy. If a future product decision binds it to Catalog, Verification must consume the finalized Catalog identifier contract rather than create a private copy.

### Sellers

Verification intentionally does not depend on Sellers. Verification is hardened before Sellers and may verify a Business account whether or not a Seller row exists. When Sellers is hardened, Sellers may consume the finalized Verification/Accounts verified contract; Verification must not create, activate, or mutate Seller state.

## Data layer

### `AOS Verification Request`

One row per account. The unique `user` schema invariant enforces this at the database layer.

| Field | Ownership | Purpose |
|---|---|---|
| `name` | Server | Stable domain request identity using `VER-.YYYY.-.#####`. Exposed publicly only as `verification_id`. |
| `naming_series` | Server | Generates Verification request identity; hidden/read-only in Desk. |
| `user` | Server | Internal Accounts/User relationship. Never accepted from the public Verification API. |
| `verification_type` | Submission | `Individual` or `Business`; immutable during review. |
| `status` | Server/reviewer | `Pending`, `Reviewing`, `Approved`, `Rejected`, or `Revoked`; transitions are centralized. |
| `submitted_on` | Server | Time of the current submission/resubmission; used for deterministic review ordering. |
| `verified_on` | Server | Server-stamped approval/rejection decision timestamp used for decision audit/dedupe; preserved if a later revocation occurs. |
| `verified_by` | Server | Internal authorized approval/rejection reviewer identity; never returned by owner APIs. |
| `revoked_on` | Server | Server-stamped revocation timestamp; separate from the original approval audit. |
| `revoked_by` | Server | Internal authorized revoking reviewer identity; never returned by owner APIs. |
| `rejection_reason` | Reviewer | Bounded reason required only for `Rejected`; owner-visible only on its own rejected request. |
| `legal_name` | Submission | Individual legal name; cleared for Business. |
| `phone_number` | Submission | Canonically validated individual phone; cleared for Business. |
| `business_name` | Submission | Business legal/trading name; cleared for Individual. |
| `business_type` | Submission | Supported business structure enum. |
| `business_category` | Submission | Bounded business classification text. It is not Seller-owned state. |
| `business_phone_number` | Submission | Canonically validated business phone. |
| `business_email` | Submission | Normalized business email. |
| `business_website` | Submission | Optional bounded HTTP/HTTPS website. Verification does not fetch it. |
| `business_address` | Submission | Bounded business address text. |
| `verification_documents` | Submission | Child rows of `AOS Verification Document`; immutable during review. |
| `submission_idempotency_key_hash` | Server | SHA-256 of the client idempotency key; raw key is never persisted. |
| `submission_payload_hash` | Server | SHA-256 of the canonical normalized submission excluding the idempotency key; detects same-key/different-payload replay. |

Manual indexes/invariants installed by `install_verification_indexes`:

- unique `user` — exactly one Verification request per account;
- `(status, submitted_on, name)` — bounded deterministic review queue scans;
- `(status, verified_on, name)` — terminal decision/operations queries;
- unique child `media` — one verification evidence Media object cannot be referenced by two Verification document rows.

### `AOS Verification Document`

Sensitive child table owned exclusively by `AOS Verification Request`.

| Field | Ownership | Purpose |
|---|---|---|
| `document_type` | Submission | Bounded human-readable document type. The product has no separate document-type master yet. |
| `document_number` | Submission | Optional bounded identifier; owner API masks it. |
| `issue_date` | Submission | Optional issue date. |
| `expiry_date` | Submission | Optional expiry date; cannot precede issue date. |
| `media` | Submission through Media contract | Canonical `AOS Media Object` identity. Must be private, owned by the account, purpose `verification_document`, and attachable to this request. |

There is no legacy attachment URL/storage field.

## State machine

New requests can only be created by `VerificationService` and always start `Pending`.

Reviewer transitions are:

- `Pending -> Reviewing`
- `Pending -> Approved`
- `Pending -> Rejected`
- `Reviewing -> Approved`
- `Reviewing -> Rejected`
- `Approved -> Revoked`

`Rejected` and `Revoked` are terminal for Desk editing. The authenticated owner can resubmit either state through the canonical submission API, which returns the existing row to `Pending`, clears prior decision metadata and replaces evidence.

`Revoked` means an existing approval was withdrawn. Pending/Reviewing requests are rejected rather than revoked. Accounts permanent-deletion cleanup may use an explicit internal system transition to revoke outstanding/approved records without impersonating a reviewer.

Status assignment is never accepted from the public client. The DocType controller reloads/locks the authoritative previous row and revalidates reviewer authority and transition legality, so a stale Desk form or `ignore_permissions=True` alone cannot bypass the state machine.

## Submission transaction and concurrency

Submission performs the following bounded transaction work:

1. normalize and strictly validate the payload;
2. lock the canonical Accounts profile (`FOR UPDATE`) and verify account lifecycle eligibility;
3. lock the current Verification request if it exists;
4. resolve exact idempotent replay vs state conflict/resubmission;
5. validate every Media ID through `MediaService` without exposing existence/ownership detail;
6. save the single Verification request and child rows;
7. release replaced evidence and attach current evidence using the Media transaction contract;
8. return a privacy-safe response.

The public API owns a savepoint and rolls back all Verification/Media database changes if any step fails. Verification services never commit the caller transaction.

The Accounts-profile-first lock order serializes the first insert as well as resubmission across horizontally scaled workers. Database uniqueness is the final invariant if competing code paths ever bypass that serialization.

### Idempotency

`idempotency_key` is mandatory for submission, 8–120 characters, and never stored raw.

- same key + same normalized payload: returns the current accepted request without creating/reopening anything;
- same key + different normalized payload: `VERIFICATION_IDEMPOTENCY_CONFLICT` (`409`);
- different key while `Pending`/`Reviewing`: `VERIFICATION_IN_PROGRESS` (`409`);
- different key while `Approved`: `VERIFICATION_ALREADY_APPROVED` (`409`);
- different key while `Rejected`/`Revoked`: starts a new submission on the same request row.

A resubmission therefore requires a fresh idempotency key.

## Evidence / Media policy

Each submission must contain 1–10 evidence rows. Every row must contain only the canonical fields `document_type`, optional `document_number`, optional dates, and `media_id`. Unknown fields are rejected, including legacy aliases such as `media`, `attachment_media`, URL/path/storage metadata, and nested storage objects.

Before a request is persisted and again at the DocType integrity boundary, evidence is validated through Media:

- caller owns the Media object;
- purpose is `verification_document`;
- visibility is `Private`;
- state is attachable (`Uploaded`) or already attached to this exact Verification request;
- it is not attached to another resource;
- duplicate Media IDs in one request are rejected;
- Media's purpose-level content type/extension/size/count rules remain authoritative.

Verification never performs object-store I/O directly.

## Review decisions

Reviewer authorization is effective backend **Write** permission on `AOS Verification Request`. The source DocType grants this to `System Manager`; deployments can delegate through standard Frappe permission configuration without introducing a client-supplied reviewer flag.

Creation/deletion/sharing are disabled on the source reviewer DocPerm. Submission identity and evidence fields are read-only in Desk and server-enforced immutable during review. Reviewers can change the status according to the state machine and supply a rejection reason when required.

Approval/revocation locks the Accounts profile before the Verification row and updates the Accounts verified projection in the same transaction. Reviewer identity/timestamps are server-derived from the authenticated reviewer session. Revocation records `revoked_by`/`revoked_on` and preserves the original approval `verified_by`/`verified_on` audit.

There is deliberately no public reviewer/admin Verification API in v1. Review is a staff Desk operation protected by Frappe permissions and server lifecycle checks, so there is no separate Verification reviewer endpoint to expose in Postman or rate-limit as a client API.

## Public API

The public v1 surface contains exactly two authenticated endpoints. `docs/features/verification/api.md` contains the code-derived inventory; this README is authoritative for semantics.

### `POST aos.api.v1.verification.submit_verification`

Frontend use: submit a first Verification request or resubmit a rejected/revoked request after all evidence has completed the Media flow.

Authentication: required session. Account identity comes only from the session.

Application limit: **5 requests per account per hour**, in addition to the shared authenticated edge baseline.

Required common fields:

- `verification_type`: `Individual` or `Business`
- `idempotency_key`: 8–120 characters matching the canonical key format
- `verification_documents`: 1–10 rows

Each document row:

- `document_type` — required
- `media_id` — required canonical Media ID
- `document_number` — optional
- `issue_date` — optional ISO date
- `expiry_date` — optional ISO date

Individual fields:

- `legal_name` — required
- `phone_number` — required canonical international phone

Business fields:

- `business_name` — required
- `business_type` — required enum
- `business_category` — required bounded text
- `business_phone_number` — required canonical international phone
- `business_email` — required
- `business_address` — required
- `business_website` — optional HTTP/HTTPS URL

Unknown fields are rejected. Known Frappe transport fields (`cmd`, CSRF/session transport values) are ignored by the domain payload validator and are never treated as business fields.

Success `data` contains:

- `verification_id`
- `verification_type`
- `status`
- `submitted_on`
- `documents[]` containing `document_type` and `media_id`

It never returns raw document numbers, internal User identity, reviewer identity, private URLs, bucket/object keys, credentials, or storage provider metadata.

### `GET aos.api.v1.verification.get_my_verification`

Frontend use: render the authenticated owner's verification status/settings screen.

Authentication: required session.

Application limit: **60 reads per account per minute**, in addition to the shared authenticated edge baseline.

Business input fields: none. Unknown fields are rejected.

Success `data` contains:

- `is_verified` derived from canonical Verification state;
- `verification`, either `null` or an owner-safe object with `verification_id`, opaque `account_id`, type, status, submission/decision/revocation times, masked evidence fields, and `rejection_reason` only when rejected.

The response is private/no-store.

## Public error contract

Verification uses the shared response envelope and stable mappings. Important domain errors include:

| Code | HTTP | Meaning |
|---|---:|---|
| `AUTH_REQUIRED` | 401 | Authenticated session required. |
| `ACCOUNT_DELETED` / `ACCOUNT_SUSPENDED` / `ACCOUNT_DISABLED` | 403 | Accounts lifecycle prevents submission/decision. |
| `PROFILE_NOT_FOUND` | 404 | Canonical Accounts profile is missing. |
| `VERIFICATION_INVALID_REQUEST` | 422 | Generic invalid Verification input. |
| `VERIFICATION_UNKNOWN_FIELD` | 422 | Unknown top-level/document field. |
| `VERIFICATION_INVALID_TYPE` | 422 | Unsupported request type. |
| `VERIFICATION_INVALID_IDEMPOTENCY_KEY` | 422 | Missing/invalid idempotency key. |
| `VERIFICATION_INVALID_DOCUMENT` | 422 | Evidence is invalid/unowned/unready/wrong purpose without leaking which condition. |
| `VERIFICATION_DUPLICATE_DOCUMENT` | 409 | Same Media ID appears more than once. |
| `VERIFICATION_IDEMPOTENCY_CONFLICT` | 409 | Same key was reused with different normalized input. |
| `VERIFICATION_IN_PROGRESS` | 409 | Another current submission is already pending/reviewing. |
| `VERIFICATION_ALREADY_APPROVED` | 409 | Approved account cannot start another submission. |
| `VERIFICATION_INVALID_STATE` / `VERIFICATION_CONFLICT` | 409 | State transition/request conflict. |
| `VERIFICATION_ACCESS_DENIED` | 403 | Reviewer/domain authorization denied. |
| `VERIFICATION_INTERNAL_ERROR` | 500 | Sanitized unexpected failure. |

Raw Frappe/database/provider exceptions and stack traces are never returned.

## Notifications

User-visible decision policy:

| Transition | Persistent notification | Reason |
|---|---|---|
| `Pending/Reviewing -> Approved` | Yes: `verification_approved` | Terminal successful decision users need to know. |
| `Pending/Reviewing -> Rejected` | Yes: `verification_rejected` | Terminal/actionable decision; owner can correct and resubmit. |
| `Pending -> Reviewing` | No | Internal process progress, not actionable enough to justify inbox noise. |
| `Approved -> Revoked` | No, currently | Product has no finalized revocation copy/action flow in the production Notifications contract. The state remains visible in owner status. Add a canonical notification type only when that UX is defined. |
| Internal account deletion revoke | No | The recipient account is unavailable/deleting; Accounts also cancels pending notification delivery work. |

Approved/rejected dedupe keys use Verification ID plus the server-stamped decision token, so retries of one decision cannot create duplicate user notifications while a later resubmission/decision can create a new one. Metadata is bounded by the canonical Notifications contract and contains only public `account_id` plus `verification_id`.

## Account deletion and restore

Recoverable Accounts deletion remains owned by Accounts. Public account serializers already hide verified state while an account is deleted. Verification does not add a parallel delete/restore API.

On permanent account cleanup, the existing Accounts deletion service:

- releases Verification evidence through Media without direct storage I/O;
- removes child evidence rows only after safe release/known absence;
- retains the Verification decision record;
- revokes outstanding/approved Verification state through an internal bounded update and stamps `revoked_on`;
- leaves mismatched/failed evidence references in place for safe idempotent repair rather than losing the only reference to sensitive Media;
- cancels pending notification delivery work for the deleted recipient through the Notifications/Accounts cleanup path.

If an account is restored during the recoverable window, existing approved Verification remains valid because the account was not permanently purged. After permanent purge/revocation there is no restore path; if future account recreation is allowed it must submit fresh evidence rather than resurrecting a revoked record.

## Security and privacy

Verification data is highly sensitive.

- public endpoints never accept an account/user argument;
- owner reads are session-scoped and no-store;
- no endpoint exposes `User.name`, `verified_by`, `revoked_by`, raw storage metadata, credentials or server paths;
- document numbers are masked in owner responses;
- reviewer-only fields remain in staff-protected DocType state;
- private evidence authorization is rechecked through Media;
- cross-user Media failures collapse to `VERIFICATION_INVALID_DOCUMENT` to avoid IDOR/existence disclosure;
- evidence is never made public by Verification;
- source DocTypes disable rename and web search indexing;
- submitted identity/evidence and idempotency metadata cannot be edited during review;
- structured Verification observability intentionally omits PII/document contents.

Verification does not claim malware/antivirus scanning because no such finalized dependency exists. It inherits the production Media validation contract for allowed content types, extensions, content parsing/validation, size and private storage.

## Retention

- Active Pending/Reviewing/Approved requests retain the current private evidence needed for review/audit.
- Rejected and Revoked requests retain evidence until resubmission or account retention cleanup so the decision record remains explainable; resubmission releases evidence no longer used.
- Released `verification_document` Media follows Media's private orphan retention/deletion policy (currently one day at the Media-purpose layer).
- Permanent account purge releases raw evidence and removes safe child references while retaining the non-document Verification decision row.
- No expiry/reverification scheduler exists because the product/schema has no verification-expiry requirement yet.

## Background/internal behavior

Verification currently has no feature-owned scheduled job or background worker. Media cleanup and Notifications delivery run in their respective production-ready domains. Internal account-deletion integration remains in the Accounts deletion service and is not a client API.

## Observability

Structured events include submission/resubmission, evidence release, review progress/decision and notification-intent outcomes. Logs use public account/Verification identifiers and bounded status/count fields only; they must not contain legal names, document numbers, phone numbers, addresses, private URLs, session IDs or raw payloads.

## Fresh-site schema installation

This project targets a fresh site. There is no Verification legacy migration/reconciliation patch.

`aos.patches.v1_0.install_verification_indexes` is a current, schema-only, idempotent installer and is also reasserted by `aos.migrate.after_migrate` after DocType synchronization. It creates the exact current indexes/uniqueness invariants and contains no historical duplicate cleanup, fallback columns or compatibility behavior.

`bench migrate` is expected to create the current DocTypes, fields and indexes directly.

## Tests and validation

Feature tests live in `aos/api/verification/tests/` and cover:

- exact public endpoint surface and HTTP methods;
- strict top-level/document input contracts and removal of legacy aliases/modules;
- canonical Accounts/Media/Notifications dependencies and absence of Sellers dependency;
- state transitions and reviewer authorization;
- server-owned immutable review/submission metadata;
- payload-aware idempotency;
- duplicate/concurrent submission invariants;
- owner isolation and private evidence validation;
- privacy-safe serialization;
- Accounts verified projection;
- approved/rejected notification dedupe/failure isolation contract;
- account deletion evidence/revocation integration;
- schema/index installer and rate-limit registry coverage;
- PII-safe observability.

Pure/static validation that does not require a Frappe site:

```bash
python -m compileall -q aos ci
python -m unittest aos.api.verification.tests.test_validation -v
python -m unittest aos.api.verification.tests.test_verification_source_guards -v
python ci/validate_api_documentation.py
python ci/validate_rate_limit_coverage.py
python ci/validate_foundation.py
python ci/validate_deployment.py
python ci/validate_repository.py
```

Authoritative migrated-site validation:

```bash
bench --site <site> migrate
bench --site <site> run-tests --app aos --module aos.api.verification
```

Then run regression coverage for production-ready dependencies touched by Verification (`accounts`, `media`, `notifications`) and finally the full AOS suite before promotion.
