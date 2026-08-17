# Accounts API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `deactivate_account` | POST | Session required | Client |
| `get_my_preference` | Any* | Session required | Client |
| `get_profile` | Any* | Session required | Client |
| `update_my_preference` | POST | Session required | Client |
| `update_profile` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

All responses use `{ok, message, data}` or `{ok, message, error, data}`. Clients branch on `error`, never message text.

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `aos.api.v1.accounts.get_profile` | Required | Private self profile when no target; bounded public profile for `target_user`/`account_id`. |
| POST | `aos.api.v1.accounts.update_profile` | Required | Strict partial profile update and avatar attach/remove. |
| GET | `aos.api.v1.accounts.get_my_preference` | Required | Account-owned country/currency/language/location preferences. |
| POST | `aos.api.v1.accounts.update_my_preference` | Required | Locked partial preference update. |
| POST | `aos.api.v1.accounts.deactivate_account` | Required | Disable access without applying deletion retention policy. |

Recoverable deletion and restoration remain under the existing Auth endpoints because Auth owns confirmation, OTP proof, and credential/session effects.

Sensitive responses send private/no-store cache headers. Stable errors include `INVALID_PROFILE_FIELD`, `INVALID_DISPLAY_NAME`, `INVALID_PHONE_NUMBER`, `COUNTRY_LOCKED`, `INVALID_AVATAR_MEDIA`, `PROFILE_UNAVAILABLE`, `ACCOUNT_DEACTIVATED`, and existing Auth/Media codes.
