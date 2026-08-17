# Search/Ranking API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `handle_callback` | POST | Guest allowed | Signed callback |
| `related_ads` | GET | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

Base method prefix: `aos.api.v1.search_ranking.`.

## `related_ads` — GET — guest allowed

Returns a bounded set of Ads related to the requested Ad while preserving Ads visibility, lifecycle, account-state, and marketplace policy. Search/ranking results are derived recommendations; canonical Ad detail remains owned by the Ads domain.

## `handle_callback` — POST — signed service callback

Receives durable companion results. It is guest-decorated only because service authentication uses the AOS signed callback contract rather than a browser/user Frappe session. It is not a general client endpoint.

See [Production search/ranking service](../../production/search-ranking-service.md) for queue/retry/callback details.
