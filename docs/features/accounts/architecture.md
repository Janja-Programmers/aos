# Accounts architecture

## Boundaries

`aos.api.v1.accounts` contains stable Frappe wrappers. `aos.api.accounts` parses requests, authenticates, rate-limits, maps domain errors, and commits or rolls back one operation. Application logic lives under `aos.services.accounts`.

- **Accounts:** profile, preferences, public identity, serializers, lifecycle state.
- **Auth:** credentials, password proof, session creation, logout, session/token revocation.
- **Localization:** active country/currency/language/location validation and serialization.
- **Media:** upload validation, storage, object identity, ownership, attachment, replacement, URLs, cleanup.
- **Seller/Verification:** business and verification workflow state; Accounts returns bounded summaries only.

## Transactions

Profile, preference, deactivation, deletion, and restoration paths lock the account-owned row before mutation. API handlers own transaction completion. Media attachment metadata participates in the same database transaction; no storage deletion occurs during avatar replacement.

## Identity

Internal foreign keys continue using `User.name`. Public surfaces use `AOS Profile.public_id`, an immutable random `ACC-*` value. Legacy email/User references remain accepted as input during migration but are resolved internally and are never emitted as public account IDs.
