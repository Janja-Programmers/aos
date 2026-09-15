# Saved Search API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `create_saved_search` | POST | Session required | Client |
| `delete_saved_search` | POST | Session required | Client |
| `list_saved_searches` | GET | Session required | Client |
| `update_saved_search` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->


The canonical Saved Search ownership, persistence, validation, execution, scale and concurrency model is defined in [Marketplace Discovery](../marketplace-discovery/README.md).

This document owns only the code-derived Saved Search HTTP inventory. It describes the current v1 contract only.

## Client contract

Saved Searches persist canonical Ads search criteria, not result IDs or ranking state. Session identity is authoritative. Create/update/delete are owner-scoped; list is bounded and cursor-paginated. Opening a saved query uses its returned canonical query with the current Ads discovery pipeline.
