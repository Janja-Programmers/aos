# Saved Searches

## Overview

Saved Searches persist an authenticated user's canonical Ads search intent. They store criteria, not results, ranking scores, cursors, or index documents. Executing a saved search means applying its returned `query` to the current Ads discovery endpoint.

## Responsibilities

Saved Searches validates canonical Ads search intent, enforces owner scope and per-user limits, deduplicates equivalent active searches using a deterministic fingerprint, provides optimistic update/delete versions, and lists active records with keyset pagination.

## Boundaries

Authentication owns session identity. Ads owns search validation/execution and public eligibility. Catalog, Localization and Sellers validate referenced search values through the Ads/search boundary. Search Ranking determines current result order at execution time; Saved Searches never snapshots ranking state.

## Architecture

```text
aos.api.v1.saved_search
        ↓
strict endpoint validation / rate limit
        ↓
aos.api.saved_search.service
        ↓
canonical Ads search-intent validator
        ↓
AOS Saved Search
```

## Data Model

### AOS Saved Search

Owner-scoped persisted search intent. Internal `name` uses `SEARCH-<uuid4hex>` without a naming-series counter. `public_id` is the client identity. `fingerprint` hashes canonical query JSON. Active-row uniqueness is enforced transactionally by locking the User row before check/create/update; `modified` is the optimistic version.

Manual owner cursor/fingerprint indexes are installed by `aos.patches.v1_0.install_marketplace_discovery_indexes` and reasserted after DocType synchronization.

## Fields

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `title` | Data | YES | list | User-visible saved-search title. |
| `public_id` | Data | NO | unique | Opaque client-facing identity. |
| `fingerprint` | Data | NO | indexed | Deterministic canonical-query digest. |
| `is_active` | Check | NO | list | Soft lifecycle flag. |
| `params_json` | JSON | YES | — | Canonical persisted Ads search intent. |
| `user` | Link → User | YES | list | Owning authenticated account. |

## API

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


All endpoints require a session and are exposed under `aos.api.v1.saved_search`.

| Endpoint | HTTP | Inputs | Output / behavior |
|---|---|---|---|
| `create_saved_search` | POST | required `title,query` | `{id,version,query}`; locks the User row, enforces max 100 active rows, rejects duplicate fingerprint |
| `update_saved_search` | POST | required `search_id,title,query,version` | `{id,version,query}`; owner-only and optimistic-version protected |
| `list_saved_searches` | GET | optional `limit,cursor` | `{items,next_cursor}` ordered by `modified DESC, public_id DESC`; limit 1–50 |
| `delete_saved_search` | POST | required `search_id,version` | `{id}` after owner/version validation; soft-deactivates the row |

The persisted `query` supports the canonical frontend Ads criteria validated by `persisted_search_intent`; execution pagination/cursors are excluded. Unknown endpoint fields fail with `SEARCH_INVALID_FILTERS`. Ownership misses return `SAVED_SEARCH_NOT_FOUND`; stale/duplicate semantic writes return Saved Search conflict codes.

## Cross-feature Dependencies

Saved Searches consumes Authentication session identity and Ads/Search canonical intent validation. Through that validation it consumes current Catalog, Localization and Seller references. Result execution remains owned by Ads/Search Ranking.

## Transaction / Concurrency Model

Create/update/delete use request-local savepoints for error rollback and do not manually commit. The User row is locked before per-user count and duplicate-fingerprint checks, serializing concurrent writes for one owner without a global lock. Updates/deletes additionally lock the selected row and compare `modified` against the supplied version. UUID-backed internal names require no shared sequence.

## Caching

Saved Searches has no feature-owned cache. Rows are read from MariaDB; execution uses the normal Ads/Search caching and ranking layers.

## Performance / Scalability

Writes are bounded by 100 active searches per user and one owner-row lock; contention is therefore isolated to concurrent mutations by the same account. Lists use keyset pagination and owner/index ordering, not unbounded offsets. Canonical query payload size is capped at 16 KiB. Multi-node safety is database-backed. Alert fan-out is not part of the current feature and must not be evaluated synchronously on every Ad write.

## Testing

Coverage is shared with marketplace-discovery and Saved Search service/contract tests, including owner scope, fingerprints, list cursors, version conflicts, bounded payloads, canonical Ads criteria and schema indexes. Shared feature fixtures remove Saved Search rows before Users. Run focused modules on a configured Frappe bench and always include `bench run-tests --app aos` in the release gate.
