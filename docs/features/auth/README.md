# Auth

Auth owns registration, email verification, password and social login, Frappe session creation/termination, password reset/change, account deletion confirmation, and deleted-account restoration.

Start with:

- [API](api.md) — complete v1 Auth request/response contract.
- [Accounts lifecycle](../accounts/account-lifecycle.md) — authoritative account-state behavior after authentication.
- [Accounts deletion](../accounts/account-deletion.md) — cross-domain cleanup and retention.
- [API versioning](../../api/versioning.md) — public namespace and compatibility rules.

The public HTTP surface is `aos.api.v1.auth.*`. Modules under `aos.api.auth.*` are implementation code and are not supported public routes.
