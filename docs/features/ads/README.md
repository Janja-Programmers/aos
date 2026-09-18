# Ads

## Overview

Ads owns the AOS marketplace listing aggregate: listing content, seller association, Catalog selections, market/location, canonical Media references, pricing/offers, moderation/review state, lifecycle, expiry, drafts, reports, public projections, owner projections, and write concurrency. Public Ads are database-authoritative; Redis, Qdrant and ranking services are derived acceleration layers and cannot make an ineligible listing visible.

## Responsibilities

Ads validates and persists listing state, enforces seller/lifecycle/version rules, projects buyer and owner response shapes, queues moderation and discovery-index work after authoritative writes, applies Ads visibility rules on every public projection, and provides bounded public discovery/read endpoints plus owner mutations.

## Boundaries

Ads consumes Authentication for session identity, Accounts for account eligibility, Localization for country/currency/location rules, Media for upload ownership/lifecycle/URLs, Sellers for seller identity/status, Catalog for category and attribute rules, Notifications for lifecycle messages, Verification for trust projection, and Search Ranking for derived candidate order. It does not implement those domains itself. Maps/geocoding is not performed inside Ads writes; persisted `AOS Location` references are validated through Localization.

## Architecture

```text
AOS API v1 Ads wrappers
        ↓
aos.api.ads endpoint orchestration
        ↓
aos.services.ads domain rules + Catalog/Media/Localization/Seller boundaries
        ↓
AOS Ad aggregate / drafts / reports + schema indexes
        ↓
after-commit moderation/search-index jobs and derived discovery services
```

`aos.services.marketplace_discovery` contains shared projection/query helpers used by Ads and Search Ranking. It is an implementation layer, not a second public Ads contract.

## Data Model

### AOS Ad

Aggregate root. Internal `name` uses `AD-<uuid4hex>` generated without a naming-series counter. `public_id` is the client identifier and is unique. `submission_key_hash` is unique and provides create idempotency. The database row is the authority for publication state.

### AOS Ad Image

Child table of canonical `AOS Media Object` references with primary/order metadata. Media validation and URL projection remain owned by Media.

### AOS Ad Attribute Value

Child table of Catalog-backed listing values plus immutable display/type/unit snapshots required to render the listing consistently.

### AOS Ad Draft

Owner-scoped posting-wizard state. Internal `name` uses `DRAFT-<uuid4hex>`; `public_id` is the client identifier. `modified` is the optimistic version.

### AOS Ad Report

Transactional report record. Internal `name` uses `RPT-<uuid4hex>`. The row links the Ad, reporter, Seller, reason, review state and administrative outcome.

Manual composite indexes and uniqueness rules used by Ads are installed by `aos.patches.v1_0.install_marketplace_discovery_indexes` and reasserted from `aos.migrate.after_migrate`.

## Fields

### AOS Ad

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `title` | Data | YES | list | Buyer-facing listing title. |
| `category` | Link → AOS Category | YES | indexed, list | Canonical Catalog category. |
| `details` | Table → AOS Ad Attribute Value | NO | — | Catalog-backed attribute values. |
| `description` | Small Text | YES | — | Buyer-facing description. |
| `currency` | Link → Currency | YES | — | Listing source currency. |
| `price_type` | Select | YES | — | Fixed, Negotiable, or Contact for price. |
| `price` | Currency | NO | — | Source amount when the price type requires one. |
| `price_unit` | Data | NO | — | Catalog-authorized unit for service pricing. |
| `video_media` | Link → AOS Media Object | NO | indexed | Canonical video Media reference. |
| `images` | Table → AOS Ad Image | NO | — | Canonical image Media references. |
| `status` | Select | NO | indexed, list | Listing lifecycle state. |
| `public_id` | Data | NO | unique | Opaque client-facing Ad identity. |
| `submission_key_hash` | Data | NO | unique | Hashed create idempotency key. |
| `review_source` | Select | NO | — | Review decision source. |
| `review_result` | Select | NO | — | Persisted moderation/review result. |
| `reviewed_by` | Link → User | NO | — | Authorized reviewer. |
| `reviewed_on` | Datetime | NO | — | Review timestamp. |
| `expires_on` | Date | NO | — | Publication expiry boundary. |
| `decline_reason` | Small Text | NO | — | Safe owner-visible decline reason. |
| `country` | Link → Country | YES | indexed | Listing market. |
| `location` | Link → AOS Location | YES | indexed | Canonical Localization location. |
| `average_rating` | Float | NO | indexed | Denormalized listing rating projection. |
| `total_reviews` | Int | NO | indexed | Denormalized review count. |
| `offer_price` | Currency | NO | indexed | Optional offer price. |
| `offer_start_date` | Date | NO | indexed | Offer start boundary. |
| `offer_end_date` | Date | NO | indexed | Offer end boundary. |
| `offer_percent` | Float | NO | — | Derived offer percentage. |
| `seller` | Link → AOS Seller | YES | indexed | Owning Seller aggregate. |
| `total_reports` | Int | NO | indexed | Denormalized report count. |
| `status_changed_on` | Datetime | NO | — | Lifecycle audit timestamp. |
| `published_on` | Datetime | NO | — | Publication timestamp. |
| `sold_on` | Datetime | NO | — | Sold timestamp. |
| `renewed_on` | Datetime | NO | — | Renewal timestamp. |
| `expired_on` | Datetime | NO | — | Expiry timestamp. |
| `deleted_on` | Datetime | NO | — | Logical-delete timestamp. |
| `wishlist_count` | Int | NO | indexed | Derived active Wishlist count. |

### AOS Ad Image

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `media` | Link → AOS Media Object | YES | indexed, list | Image Media identity. |
| `is_primary` | Check | NO | list | Primary-card/detail image marker. |
| `sort_order` | Int | NO | list | Stable presentation order. |

### AOS Ad Attribute Value

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `attribute` | Link → AOS Ad Attribute | NO | list | Catalog attribute identity. |
| `value_text` | Data | NO | — | Text/select value. |
| `value_json` | Small Text | NO | — | Structured/multi value. |
| `value_number` | Float | NO | — | Numeric value. |
| `value_date` | Date | NO | — | Date value. |
| `value_bool` | Check | NO | — | Boolean value. |
| `attribute_key` | Data | NO | — | Stable Catalog key snapshot. |
| `attribute_label` | Data | NO | — | Display label snapshot. |
| `attribute_type` | Data | NO | — | Field-type snapshot. |
| `attribute_unit` | Data | NO | — | Unit snapshot. |

### AOS Ad Draft

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `public_id` | Data | NO | unique | Opaque client-facing draft identity. |
| `status` | Select | NO | — | Draft/Submitted/Abandoned lifecycle. |
| `payload_json` | JSON | YES | — | Canonical posting payload. |
| `title_hint` | Data | NO | — | List-preview title. |
| `country_hint` | Link → Country | NO | — | List-preview market. |
| `last_step` | Int | NO | — | Wizard resume position. |
| `category_hint` | Link → AOS Category | NO | list | List-preview category. |
| `location_hint` | Link → AOS Location | NO | — | List-preview location. |
| `submitted_ad` | Link → AOS Ad | NO | list | Resulting Ad after submission. |
| `user` | Link → User | YES | indexed, list | Draft owner. |

### AOS Ad Report

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `ad` | Link → AOS Ad | YES | indexed, list | Reported Ad. |
| `reported_by` | Link → User | YES | indexed, list | Reporting account. |
| `seller` | Link → AOS Seller | YES | list | Seller snapshot/reference. |
| `reason` | Link → AOS Report Reason | YES | — | Canonical report reason. |
| `status` | Select | NO | indexed, list | Review lifecycle. |
| `details` | Small Text | NO | — | Optional bounded detail. |
| `admin_action` | Select | NO | — | Administrative outcome. |
| `reviewed_by` | Link → User | NO | — | Reviewer. |
| `reviewed_on` | Datetime | NO | — | Review timestamp. |

## Declined owner projection

Seller-owned Ads reads expose `decline_reason` for declined listings. `get_my_ad` returns the complete editable Ad projection using Catalog attribute IDs in each detail row so the edit workflow can restore saved attribute values. Public buyer reads remain restricted by Ads visibility rules and do not expose declined Ads.

## API

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


All client endpoints are under `aos.api.v1.ads` and pass through `aos.api.shared.transport.execute_endpoint`. Unknown request fields fail closed.

| Endpoint | HTTP/Auth | Inputs | Canonical output / behavior |
|---|---|---|---|
| `create_ad` | POST/session | canonical create fields; `idempotency_key` required; Catalog/Media/location rules determine conditional required values | `{id,status,version,review_job_queued}`; duplicate idempotency key replays the accepted result |
| `list_ads` | GET/guest | `country,currency,location,category,seller,attributes,q,price_type,promotion_type,price_min,price_max,rating_min,verified_seller,sort,limit,offset,cursor` | bounded eligible buyer cards + pagination |
| `search_ads_by_image` | POST/guest | multipart image plus `limit,country,currency,location` | eligible buyer cards ordered from derived visual candidates |
| `get_ad` | GET/guest | `ad_id`, optional display `currency,country` | eligible buyer detail |
| `related_ads` | GET/guest | `ad_id`; optional `limit,country,currency,location` | same-category eligible related items; Search Ranking may degrade to bounded DB fallback |
| `list_my_ads` | GET/session | `limit,offset,status` | owner list |
| `get_my_ad` | GET/session | `ad_id` | owner edit projection |
| `update_ad` | POST/session | `ad_id,version` plus lifecycle-allowed fields | updated version; stale versions fail with conflict |
| `transition_ad` | POST/session | `ad_id,action,version` | explicit lifecycle transition |
| `review_ad` | POST/authorized reviewer | `ad_id,decision,reason,version` | review transition |
| `upsert_ad_draft` | POST/session | `draft_id?`, canonical `payload,last_step,version?` | draft id/version |
| `list_my_ad_drafts` | GET/session | `limit,offset` | owner draft previews |
| `get_my_ad_draft` | GET/session | `draft_id` | canonical round-trippable payload |
| `abandon_ad_draft` | POST/session | `draft_id,version` | idempotent abandonment |
| `submit_ad_draft` | POST/session | `draft_id,version` | resulting public Ad id |

### Frappe Desk manual review

The `AOS Ad` Desk form keeps `status` read-only and exposes lifecycle-safe review actions to users with `write` permission on `AOS Ad`. **Approve Ad** is shown for `Reviewing` and `Declined`; **Reject Ad** is shown for `Reviewing` and `Active` and requires a rejection reason. Both actions call the canonical `aos.api.v1.ads.review_ad` endpoint with the Ad `public_id` and current `modified` version, so authorization, optimistic concurrency, lifecycle validation, review metadata, Notifications, and discovery refresh remain server-owned. Terminal and suspended states expose no manual-review action.

Errors use the shared AOS response envelope and Ads/Search error codes. Public reads always recheck current Ad/Seller/Account/expiry/block eligibility. Draft and owner endpoints never accept another user's records.

## Cross-feature Dependencies

- **Authentication / Accounts:** identity, enabled/account state.
- **Localization:** market preferences, country/currency validation, active location validation and labels.
- **Media:** media ownership, attachment, lifecycle, content policy, URL projection.
- **Sellers:** seller creation/status/identity.
- **Catalog:** sellable categories, pricing rules, attributes/dependencies.
- **Notifications:** user-facing Ad lifecycle notifications through the Notifications service.
- **Verification:** seller trust projection used by public filtering/projection.
- **Maps:** provider/routing capabilities remain outside Ads persistence.
- **Search Ranking:** bounded candidate order and derived indexes only.
- **Wishlist:** consumes Ads eligibility/projection; Ads stores the derived count.

## Transaction / Concurrency Model

Ad creation uses a unique `submission_key_hash`; duplicate-key races are handled as idempotent replays. UUID-backed internal names require no naming-series lock. Owner/reviewer mutations lock the aggregate where needed and use optimistic `modified` versions. Draft submission derives a stable create idempotency key from the draft identity. Catalog/Media/Seller invariants are enforced before or during the authoritative write. Expensive moderation/search/vector work is dispatched outside the primary write transaction through durable jobs/outbox semantics. Normal request code does not manually commit.

## Caching

Authorization/publication truth is not cached as authoritative state. Currency/localization and Media URL helpers may use their owning feature caches. Search/vector caches are derived and disposable. Stale derived data is filtered by the authoritative Ads eligibility projection.

## Performance / Scalability

Hot writes avoid Frappe naming-series allocation and redundant location-country reads. Create idempotency is backed by a unique constraint. Public read paths use bounded page sizes, targeted composite indexes, batch Media projection, and bounded ranking candidate headroom. Derived-index work is asynchronous. Horizontal application nodes share MariaDB/Redis/services and do not rely on process-local counters or locks. Capacity at one million users still requires production-like load, queue, database and cache testing.

## Testing

Primary tests live in `aos/api/ads/tests`, `aos/tests/test_marketplace_discovery_*`, `aos/tests/test_user_action_uniqueness.py`, and shared feature integration tests. Fixtures use valid Users/Sellers/Categories/Locations/Media and exact tracked identities; teardown removes dependent reports, Wishlist rows, child rows, search/moderation jobs, Media, Sellers and Users in dependency order.

Focused command on a configured Frappe bench:

```bash
bench run-tests --app aos --module aos.api.ads.tests
```

Run the full app suite before release:

```bash
bench run-tests --app aos
```

Administrative Desk review uses the canonical Ads review endpoint and requires an authenticated Frappe user with `AOS Ad` write permission. It does not require an `AOS User Preference`.
