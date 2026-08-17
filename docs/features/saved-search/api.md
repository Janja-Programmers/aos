# Saved Search API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `delete_saved_search` | POST | Session required | Client |
| `list_saved_searches` | Any* | Session required | Client |
| `save_search` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

Base method prefix: `aos.api.v1.saved_search.`. All endpoints require an authenticated active account and use the normal AOS `{ok,message,data}` / `{ok,message,error,data}` envelope.

## Save a search

### `save_search` — POST

Accepted fields:

- `title` — required non-empty string, bounded by the server constant.
- `params_json` — required JSON object containing Ads-search parameters. The object must be JSON serializable and is size-bounded before persistence.

The backend derives a stable fingerprint from `params_json`. The final duplicate check and insert are serialized on the authenticated Frappe User row, so concurrent retries for the same user/search reuse one active record.

If the same active search already exists, the endpoint succeeds with the existing `id`. Each user also has a bounded number of active Saved Searches; callers must delete an existing entry before exceeding the limit.

## List saved searches

### `list_saved_searches`

Accepted fields:

- `limit` — optional positive page size; the backend clamps it to the configured maximum.
- `offset` — optional non-negative offset.

Ordering is deterministic: `last_used desc, name desc`.

Response data:

```json
{
  "items": [],
  "has_more": false,
  "limit": 20,
  "offset": 0,
  "next_offset": null
}
```

## Delete a saved search

### `delete_saved_search` — POST

Accepted field:

- `id` — Saved Search document identifier.

Only the owning user may delete the search. Deletion is a soft delete (`is_active=0`), so inactive records are excluded from normal list and duplicate-reuse behavior.

## Stable errors

Common domain-visible errors include `VALIDATION_ERROR`, `SAVED_SEARCH_LIMIT_REACHED`, `FORBIDDEN`, the shared authentication/account-state errors, and the shared rate-limit response.
