# Marketplace Discovery backend

This document is the canonical current-state backend contract for **Ads**, **Search Ranking**, and **Saved Searches**. It describes the fresh-site architecture only; there are no legacy aliases or migration-era contracts.

## Ownership and boundaries

**Ads** owns the listing aggregate, seller association, Catalog selections, original price/currency, selected listing location, Media references, lifecycle, publication eligibility, review state, versions, expiry and public/owner projections. Ads never owns Seller profile data, files, Catalog schema, geocoding, user preferences, notification storage, Verification workflows, Social workflows, moderation policy, FX provider state, or search/vector indexes.

**Search Ranking** owns bounded candidate generation and primary relevance/quality ordering. Redis/Qdrant documents, embeddings and scores are derived and disposable. They can never make an Ad public. Every candidate is rechecked against the Ads database before projection.

**Saved Searches** owns a user's persisted canonical search intent. It stores criteria, not results, scores, normalized index documents or ranking formulas. Opening a Saved Search means applying its returned `query` to the current canonical Ads listing/search endpoint.

Dependencies remain owned by their production-ready domains: Localization supplies countries/currencies/preferences; Authentication supplies session identity; Accounts supplies account eligibility; Sellers supplies seller identity/status; Media owns storage and lifecycle; Catalog owns categories/attributes/dependencies; Notifications supplies user-facing lifecycle notifications; Verification supplies trust projection; Social may supply ranking signals through a service boundary; Maps/Localization own location primitives; Moderation owns moderation policy/workflows. **Moderation itself is out of scope**; Ads only consumes automatic outcomes and supplies a separate authorized manual-review action.

## Data model and invariants

`AOS Ad` is the aggregate root. `public_id` is its opaque public identity; Frappe `name` never crosses the public Marketplace Discovery boundary. `submission_key_hash` provides create idempotency. `AOS Ad Image` and `AOS Ad Attribute Value` are canonical child selections; unique indexes prevent duplicate Media references or duplicate attributes per Ad. Media rows contain Media IDs only, never storage paths, signed URLs or object keys. `AOS Ad Draft` has its own opaque public ID and optimistic version. `AOS Saved Search` has an opaque public ID, owner, canonical JSON query, fingerprint, active flag and optimistic version. `AOS Exchange Rate` stores one coherent rate snapshot version across currencies.

The original `price` and `currency` on an Ad are business state and are never rewritten because exchange rates change. Display conversion and cross-currency comparison are derived.

## Ads lifecycle

Draft posting state is stored separately in `AOS Ad Draft`. Submission creates an `AOS Ad` in `Reviewing`.

Canonical listing states are `Reviewing`, `Active`, `Declined`, `Sold`, `Expired`, `Deleted`, and `Suspended`.

Seller actions are:

- `mark_sold`: `Active -> Sold` (already `Sold` is idempotent).
- `mark_available`: `Sold -> Active` (already `Active` is idempotent).
- `renew`: `Expired -> Active` and refreshes expiry.
- `delete`: `Reviewing|Declined|Sold|Expired -> Deleted` (`Deleted` is idempotent).

System actions are automatic moderation allow/reject/review, seller resubmission, expiry and suspension. Public clients cannot assign `status` directly. Every state-changing owner/manual-review request locks the aggregate, validates the loaded version, validates the transition, saves transactionally and schedules derived-index work after the durable domain write.

### Manual review

Manual review is an explicit domain action accepting `approve` or `reject`. It requires an authenticated user with canonical write capability for `AOS Ad`, an exact Ad version, and a rejection reason when rejecting. A human may approve `Reviewing` or automatically `Declined` content, and may reject `Reviewing` or automatically `Active` content; this provides a real human override without adding parallel states. Human decisions set `review_source=Manual`, `review_result`, `reviewed_on`, and the actual authenticated `reviewed_by`.

Automatic moderation uses `review_source=Automatic`, records its result/time, and deliberately leaves `reviewed_by` empty. It never fabricates an Administrator or current session user. The separate Moderation domain remains authoritative for automatic policy.

## Search pipeline

The reusable discovery order is:

```text
bounded candidate generation
-> canonical Ads/Seller/Account/expiry/block/Catalog eligibility
-> primary text/category/attribute/quality/business relevance
-> FINAL geographic reranking
-> deterministic tie-break
-> bounded pagination/projection
```

Redis candidate generation may use text, category, attributes, price/quality/trust/freshness and current valid business signals. It does not decide public visibility. Qdrant supplies image-similarity candidates only.

### Final geographic reranking

Geography is intentionally the final reranking stage. For viewer context `country=Kenya, location=Mombasa`, bucket order is:

```text
0 exact Mombasa + Kenya
1 elsewhere in Kenya
2 remaining eligible global results
```

The reranker is stable: the primary ranking order is preserved inside each bucket. SQL public listing uses the equivalent outermost bucket expression before its deterministic primary/tie order. The same policy is used for standard search/category discovery, Related Ads and image-search projection.

## Related Ads

`aos.api.v1.ads.related_ads` is the canonical detail-page endpoint. The source Ad is excluded. Search Ranking generates bounded candidates using relatedness signals (category, shared Catalog selections, text, price proximity and quality signals supported by the current index). The Ads projection then rechecks canonical eligibility, excludes the source, batch-projects Media, and applies geography last. If the optional ranking service is unavailable, a bounded authoritative category/quality candidate fallback is used; optional semantic infrastructure failing never exposes an ineligible Ad.

## FX contract

FX refresh is background operational work, not a hot-read provider call. One provider response is written as a coherent snapshot with `base_currency`, `rate_version`, `provider_timestamp` and per-currency rates. Obsolete currencies from the previous snapshot are removed in the same transaction. Cache keys are site-scoped and versioned; cache invalidation happens after commit.

Search conversion accepts only a coherent, sufficiently fresh snapshot. `provider_timestamp`, not local write time, determines freshness. Missing/unsupported/stale rates fail conversion deterministically (`FX_RATE_UNAVAILABLE` where the requested operation requires conversion). Same-currency display requires no external rate. Cross-currency filtering/sorting uses normalized SQL expressions derived from one snapshot; raw nominal values from different currencies are never compared as equivalent. Provider failure leaves the last stored snapshot untouched; reads may use it only while it satisfies the configured freshness bound.

## Image search / Qdrant

Canonical flow:

```text
AOS Ad + canonical Media IDs
-> after-commit queue task
-> Media batch URL projection for service fetch
-> embedding model
-> Qdrant derived generation
-> bounded vector candidates (public Ad IDs only)
-> authoritative Ads eligibility recheck
-> final geographic rerank
-> canonical public projection
```

Qdrant payload stores public Ad ID, Media ID, deterministic Ad generation, embedding version and collection schema version. It never stores an Ads storage key/path as canonical identity. Point IDs are deterministic. A replacement builds the complete new generation before upsert, promotes it, then removes older generations. Delayed older retries are ignored/deleted rather than replacing a newer generation. Search filters by the configured embedding/schema versions. Hard deletion captures the public Ad ID before the database row disappears and queues deletion from both derived indexes.

Qdrant outage or embedding failure cannot roll back a valid Ads database transaction. Image search may return `IMAGE_SEARCH_UNAVAILABLE`; it must never trust a stale vector for authorization or publication state. Reindex/cleanup tasks are bounded maintenance operations and are not public APIs. Frappe-to-image-search calls use finite configurable retries with bounded exponential backoff for transient transport/429/5xx failures; vector generations and delete operations are idempotent so retries converge rather than duplicate state.

The repository Qdrant service is digest-pinned, persistent, health-checked, resource-limited, log-rotated, private-by-default (loopback host binding), and API-key protected. The image-search service is private-by-default, waits for Qdrant health, uses signed internal mutations, has bounded upload/search limits and declares embedding/schema versions explicitly. Production sizing is deployment-specific; Qdrant storage/worker capacity and embedding throughput must be sized from measured corpus/query rates rather than staging hardware.

## Saved Searches

Persisted fields are the canonical frontend-supported search criteria: query text, category, Catalog attributes, seller public ID, country/location, display currency, price/rating/promotion/verification filters and sort. Pagination/cursors are execution state and are not persisted. Current references are validated through their owning domains at create/update time; if Catalog/location/currency/Seller state evolves later, current search validation/eligibility determines execution behavior. Ranking changes require no Saved Search migration.

Session identity is always the owner; client owner/account IDs are not accepted. Owner reads use opaque IDs and mask cross-owner lookup as `SAVED_SEARCH_NOT_FOUND`. Creation serializes on the User row, limits active saved searches per account, fingerprints canonical intent to reject duplicates, and stores no results. Update/delete use row locks and optimistic versions. Listing is bounded and uses a `(modified, public_id)` cursor.

Saved-search alert evaluation, if added later, must consume batched events/current Notifications and must not synchronously evaluate every Saved Search on every Ad write. No alert scheduler is introduced by this pass.

## Public/frontend-consumed API v1

All methods below use `aos.api.shared.transport.execute_endpoint`. Frappe transport metadata such as `cmd` is removed there; other unknown client fields reach strict validation and are rejected. GET methods have no request bodies.

| Method | Frappe method | Auth | Purpose |
|---|---|---|---|
| POST | `aos.api.v1.ads.create_ad` | login | create/submit canonical Ad; requires posting fields + `idempotency_key` |
| GET | `aos.api.v1.ads.list_ads` | guest/login | public Ads search/category/discovery with bounded filters/pagination |
| GET | `aos.api.v1.ads.get_ad` | guest/login | canonical public Ad detail by public `ad_id` |
| GET | `aos.api.v1.ads.related_ads` | guest/login | bounded canonical Related Ads for detail page |
| POST | `aos.api.v1.ads.search_ads_by_image` | guest/login | image upload -> eligible public Ads |
| GET | `aos.api.v1.ads.list_my_ads` | login | bounded owner list |
| GET | `aos.api.v1.ads.get_my_ad` | login | owner edit/detail projection by public `ad_id` |
| POST | `aos.api.v1.ads.update_ad` | login | optimistic owner edit (`ad_id`, `version`, canonical fields) |
| POST | `aos.api.v1.ads.transition_ad` | login | seller lifecycle action (`ad_id`, `action`, `version`) |
| POST | `aos.api.v1.ads.upsert_ad_draft` | login | create/update canonical draft |
| GET | `aos.api.v1.ads.list_my_ad_drafts` | login | bounded owner draft list |
| GET | `aos.api.v1.ads.get_my_ad_draft` | login | owner draft detail |
| POST | `aos.api.v1.ads.abandon_ad_draft` | login | optimistic draft abandon |
| POST | `aos.api.v1.ads.submit_ad_draft` | login | optimistic/idempotent draft submission |
| POST | `aos.api.v1.saved_search.create_saved_search` | login | persist canonical search intent |
| POST | `aos.api.v1.saved_search.update_saved_search` | login | optimistic update |
| GET | `aos.api.v1.saved_search.list_saved_searches` | login | cursor-paginated owner searches |
| POST | `aos.api.v1.saved_search.delete_saved_search` | login | optimistic soft delete |

`review_ad` is versioned but is an **authorized administrative/Desk action**, not a marketplace-client endpoint and must not be added to consumer Postman unless the finalized admin frontend explicitly consumes it. It accepts `ad_id`, `decision`, `reason`, `version` and enforces reviewer capability server-side.

Public list search supports the canonical intent fields `q`, `country`, `currency`, `location`, `category`, `seller`, `attributes`, `price_type`, `promotion_type`, `price_min`, `price_max`, `rating_min`, `verified_seller`, `sort`, plus bounded `limit`, `offset` and eligible recent cursor usage. Deep offset is capped; deterministic ordering/ties use public IDs. Public projection contains only marketplace fields, opaque Seller/Media IDs and derived public URLs; it does not expose internal workflow/audit/Qdrant/cache/job/Frappe metadata.

## Internal/service/maintenance interfaces — NOT Postman

- `aos.api.v1.search_ranking.handle_callback`: signed companion callback.
- Search Ranking `/jobs`, `/ads/search`, `/ads/related` companion service calls.
- Image Search `/ads/{public_ad_id}/replace-images` and `/ads/{public_ad_id}/vectors`: signed internal mutation endpoints.
- Image Search health/readiness/metrics endpoints.
- Search/Image reindex, stale-vector cleanup, outbox dispatch/recovery and queue tasks.
- FX provider refresh and scheduled refresh jobs.
- Qdrant itself and Redis ranking storage.
- cache warming/recovery/debug operations.

## Errors and concurrency

Marketplace Discovery maps domain failures into the standard AOS envelope and stable codes such as `AD_NOT_FOUND`, `AD_NOT_OWNED`, `AD_CONFLICT`, `INVALID_AD_STATE`, `INVALID_AD_CATEGORY`, `INVALID_AD_ATTRIBUTES`, `INVALID_AD_MEDIA`, `INVALID_AD_PRICE`, `INVALID_AD_CURRENCY`, `INVALID_AD_LOCATION`, `REVIEW_NOT_ALLOWED`, `SEARCH_INVALID_FILTERS`, `SEARCH_UNAVAILABLE`, `IMAGE_SEARCH_UNAVAILABLE`, `SAVED_SEARCH_NOT_FOUND`, `SAVED_SEARCH_CONFLICT`, `SAVED_SEARCH_LIMIT_REACHED`, and `FX_RATE_UNAVAILABLE`. Raw Frappe/provider/Qdrant exceptions are not public.

Owner/review/draft/Saved Search semantic writes use row locks and optimistic versions where stale writes would change domain meaning. Ad creation and derived-index delivery are idempotent. Expensive external vector/ranking work is not performed inside the primary Ads transaction. Duplicate/stale derived-index jobs converge through deterministic idempotency/generation keys.

## Performance, indexes and caching

Public reads are bounded (`MAX_PAGE_SIZE=50` for Ads; Saved Search max 50), Media is batch projected, and derived candidate lists have bounded headroom. Manual schema indexes cover public recent/category/country/location queries, owner status lists, price filtering, expiry, Ad child order/uniqueness, drafts, Saved Search owner cursor/fingerprint, search-index job state/target and FX snapshots. The schema-only installer is executed as a fresh-site patch and reasserted after every migrate.

Seller status/name, account status, account enablement, and verification changes schedule bounded keyset batches that reproject the Seller's Ads into disposable discovery indexes. The finalized dependency domains remain authoritative and do not perform discovery work inline.

Derived index/cache entries are never sufficient for visibility. Canonical eligibility recheck fails closed, so stale Redis/Qdrant/cache data cannot resurrect Reviewing/Declined/Sold/Expired/Deleted/Suspended Ads or Ads owned by ineligible Sellers/accounts. FX cache is site-scoped/versioned. Authorization decisions are not cached as public listing truth.

## Operational recovery

Redis/Qdrant are recoverable from the database and canonical Media references. Reindex jobs are bounded/idempotent. Queue backpressure delays discovery freshness but does not corrupt Ads. Companion requests have finite timeouts/retries. Stale generations cannot overwrite newer vector generations. A full Qdrant collection can be rebuilt under a new collection/schema version and traffic switched by configuration after reindex validation.
