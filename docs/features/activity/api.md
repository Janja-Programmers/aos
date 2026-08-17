# Activity API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `clear_activity` | POST | Session required | Client |
| `hide_activity` | POST | Session required | Client |
| `list_activity` | GET | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## `list_activity`

Lists only the authenticated user's `Active` Activity Center rows. Optional existing aliases remain supported:

- `group` / `activity_group`
- `type` / `activity_type`
- `limit`
- `start`

Conflicting aliases are rejected. `limit` defaults to 20 and is bounded to 50; `start` is non-negative and bounded. Group/type values are validated against the repository's actual groups/emitted activity types.

Rows are ordered by latest occurrence, then creation/name for deterministic pagination. Responses are private/no-store and pass through the privacy-safe Activity serializer.

## `hide_activity`

Hides one authenticated user's row using `activity_id` / legacy `id`. The operation is owner-scoped, row-locked, and idempotent. Another account receives the same not-found behavior as an unknown ID, preventing IDOR enumeration.

## `clear_activity`

Clears the authenticated user's active history, optionally filtered by the existing group/type aliases. Rows are processed in bounded locked batches. Clear never affects another account.

## Transactions and errors

The API owns no outer transaction commit. Hide/clear use operation savepoints and roll back only their own partial work on failure. Existing response envelopes and generic Activity error codes (`VALIDATION_ERROR`, `NOT_FOUND`, `INTERNAL_ERROR`) are preserved; raw Frappe exceptions are never returned.

All three endpoints require an active authenticated AOS account and explicit rate-limit policy.
