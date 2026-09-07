# Accounts architecture

## Boundaries

`aos.api.v1.accounts` contains stable Frappe wrappers. `aos.api.accounts` parses requests, authenticates, rate-limits and maps domain errors. Application logic lives under `aos.services.accounts`.

- **Accounts:** canonical AOS profile, stable account identity, public/private serializers and lifecycle state.
- **Auth:** credentials, password proof, 2FA continuation, session creation/logout and access revocation.
- **Localization:** canonical country/currency/language/location selection. Accounts does not duplicate location.
- **Media:** upload validation, storage identity, ownership, attachment/replacement and cleanup.
- **Seller/Verification:** workflow state; Accounts returns bounded projections only.

## Transactions

Profile and lifecycle mutations lock the account-owned row before mutation. API handlers own transaction completion. Media attachment metadata participates in the same database transaction; no storage deletion occurs during avatar replacement.

## Identity

Frappe `User.name` is the internal authentication identity. `AOS Profile.name` is the immutable opaque `ACC-*` account id and `AOS Profile.user` is the unique link back to `User`. Public account-reference inputs accept `ACC-*` only; email/User.name is never a public account id.

## Data ownership

`AOS Profile` is canonical for display name, bio, phone, date of birth, gender and profile media. Frappe `User` receives only deliberate framework projections for display name and avatar. Localization owns location. Verification Request owns verification audit metadata while `AOS Profile.is_verified` is a deliberate hot-read projection.
