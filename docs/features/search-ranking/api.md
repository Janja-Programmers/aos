# Search Ranking API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `handle_callback` | POST | Guest allowed | Signed callback |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->


The canonical Search Ranking pipeline, Related Ads behavior, final geographic reranking, derived-index semantics and Qdrant/Redis recovery rules are defined in [Marketplace Discovery](../marketplace-discovery/README.md).

This document owns only the code-derived Search Ranking HTTP inventory. It describes the current service boundary only.

## Service contract

Search Ranking candidate generation is internal. The versioned HTTP callback is a signed worker/service interface and is not a frontend/Postman endpoint. Related Ads is frontend-consumed through `aos.api.v1.ads.related_ads`, not through a duplicate Search Ranking public route.
