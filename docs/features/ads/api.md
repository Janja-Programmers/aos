# Ads API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `abandon_ad_draft` | POST | Session required | Client |
| `create_ad` | POST | Session required | Client |
| `get_ad` | GET | Guest allowed | Client |
| `get_my_ad` | GET | Session required | Client |
| `get_my_ad_draft` | GET | Session required | Client |
| `list_ads` | GET | Guest allowed | Client |
| `list_my_ad_drafts` | GET | Session required | Client |
| `list_my_ads` | GET | Session required | Client |
| `related_ads` | GET | Guest allowed | Client |
| `review_ad` | POST | Session required | Client |
| `search_ads_by_image` | POST | Guest allowed | Client |
| `submit_ad_draft` | POST | Session required | Client |
| `transition_ad` | POST | Session required | Client |
| `update_ad` | POST | Session required | Client |
| `upsert_ad_draft` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->


The cross-domain Marketplace Discovery architecture, lifecycle, ranking, FX, Qdrant, projection, concurrency and operational rules are canonical in [Marketplace Discovery](../marketplace-discovery/README.md).

This document owns only the code-derived Ads HTTP inventory and Ads-specific client usage notes. It describes the current v1 contract only.

## Client contract

Frontend marketplace consumers use the versioned `aos.api.v1.ads` methods. Public identifiers are opaque `ad_id`/draft IDs; Frappe document names are never client identifiers. Mutations use canonical strict request fields, session identity, lifecycle actions, and optimistic versions where applicable. `review_ad` is an authorized administrative/Desk action rather than a marketplace consumer endpoint.
