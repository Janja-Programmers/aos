# Verification API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_my_verification` | GET | Session required | Client |
| `submit_verification` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

All Verification API semantics, fields, responses, errors, rate limits, idempotency, state rules and security requirements are canonical in [`README.md`](README.md). This file exists to host the repository-generated endpoint inventory only.
