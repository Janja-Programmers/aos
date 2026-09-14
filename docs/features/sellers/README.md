# Sellers

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_my_seller_status` | GET | Session required | Client |
| `get_seller` | GET | Guest allowed | Client |
| `get_seller_location` | GET | Guest allowed | Client |
| `list_seller_map_points` | GET | Guest allowed | Client |
| `list_sellers` | GET | Guest allowed | Client |
| `remove_my_seller_location` | POST | Session required | Client |
| `set_my_seller_location` | POST | Session required | Client |
| `update_my_seller` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->


## Purpose and ownership

Sellers is the marketplace storefront aggregate for one canonical AOS account. It owns the opaque public Seller identity, operational lifecycle, storefront profile, saved storefront location, Seller-owned concurrency versions, and Seller-facing aggregate counters. A Seller has exactly one canonical owner through `AOS Seller.user`; staff/team membership is intentionally not implemented because it is not a current product requirement.

Sellers does **not** own authentication, account identity, media storage, geocoding/routing providers, verification decisions, notification delivery, social relationships, or Catalog taxonomy. It consumes the production contracts of Authentication, Accounts, Media, Maps, Verification, Notifications, Social, and Catalog instead of duplicating them.

## Identity

The only public Seller identifier is an immutable opaque ID matching `SELLER-[A-Z2-7]{20}`. Internal Frappe document names, User IDs/e-mail addresses, database row names, DocType names, and numeric identifiers are never accepted as public Seller references. Fresh Seller documents use random Frappe document names (`autoname: hash`) and receive a random public ID at insert time. There is no migration fallback or legacy Seller-reference resolver.

## Lifecycle

Operational status is a single state machine and is separate from Verification:

| Current | Allowed next | Meaning |
| --- | --- | --- |
| Active | Suspended, Closed | Storefront can operate and mutate. |
| Suspended | Active, Closed | Storefront is hidden/blocked from operational actions until an authorized reactivation. |
| Closed | none | Terminal operational state. |

Creation is idempotent per account and produces `Active`. The database unique constraint on `user` is the final concurrency guard. Status changes are locked, require a reason/source, reject illegal transitions deterministically, and emit Seller status notifications. Repeating the current status is idempotent. `Closed` is terminal.

Verification is an orthogonal fact. `AOS Verification Request` remains canonical for Pending/Reviewing/Approved/Rejected/Revoked. An approved Business verification may project the Seller type/category; rejection or revocation does not silently suspend, close, or reactivate the Seller. Account verification, business verification, and Seller operational status therefore cannot contradict one another through independent booleans.

## Public and private model

Public Seller reads expose only reviewed storefront data: `seller_id`, privacy-safe Account display identity, Seller type/category, verification projection, Media-backed banner URL, public storefront text, operating hours, explicit saved storefront location, bounded marketplace aggregates, and Social relationship projection. Seller discovery exposes coarse location plus optional distance; the direct Seller/location endpoint may expose exact coordinates because they represent an explicitly published storefront destination used for directions.

Owner-only mutation/concurrency data includes Media object references, storefront version/timestamp, and location version/timestamp. Internal status reason/source, Verification evidence/documents, private account/contact data, internal row names, raw provider fields, and infrastructure information are not public Seller fields. Unknown request fields are rejected.

Storefront text is Unicode-normalized, bounded, plain text only, and rejects control/invisible characters, HTML tags, and script/data URL schemes. Operating hours use canonical full weekday names only.

## Authorization

Authentication/session identity comes from the hardened Auth boundary. Owner access is centralized through `AOS Seller.user`; public reads use the Social block boundary and active Account/Seller checks. Seller APIs never trust client-supplied owner IDs. Direct ownership mutation is forbidden by the DocType persistence boundary. There is no Seller-specific token/session system and no speculative staff-role model.

## Media integration

Seller banner media is stored only as `shop_banner_media`, a canonical `AOS Media Object` reference. Upload/storage, ownership checks, public URL projection, attach/release lifecycle, and replacement handling use `MediaService`. The old cached `shop_banner` URL field and request aliases are removed. The public response may include `shop_banner_url`, derived from Media, and owner mutation responses may include `shop_banner_media_id`.

## Maps and Seller location

Sellers owns Seller location records and all mutation business rules. Maps owns reusable WGS84 coordinate validation, reverse geocoding/search, provider abstraction, routing, viewport primitives, and clustering. Seller code never calls Photon/Nominatim directly and never stores provider-specific fields.

Saving a location is an explicit authenticated owner action. The request contains canonical latitude/longitude plus optional public name/instructions and optional `expected_version`. Coordinates are globally valid WGS84 values; longitude/latitude GeoJSON ordering remains a Maps concern. Maps resolves a normalized address snapshot; Seller then locks the Seller row, checks status and optimistic concurrency, persists latitude, longitude, display address, locality, region, ISO alpha-2 country code, timestamp, and increments `location_version` atomically. An identical retry is idempotent and does not geocode or increment the version. Removal is similarly locked/versioned and idempotent.

Device GPS is never persisted automatically. APIs contain no country bounding restriction. Precise coordinates are not written to Seller operational logs. Seller-owned location indexes support country, region/locality, and bounded latitude/longitude viewport queries including antimeridian-aware filtering.

Maps routing resolves `destination_seller_id` through `aos.services.sellers.repository.get_route_destination`. A valid active Seller with a persisted location therefore supports `refresh_route`; a missing/hidden/unlocated Seller continues to produce the existing Maps `MAP_LOCATION_NOT_FOUND` contract.

## Verification, Notifications, Catalog and Social

Verification decisions are read from the canonical Verification request and projected without creating a Seller verification subsystem. Seller lifecycle changes use `NotificationService.notify_seller_status_changed`; delivery failures are isolated by the hardened Notifications boundary and must not corrupt committed Seller state. Social owns follow/friend/block state; Sellers reads that state in bounded bulk form and does not duplicate it. Catalog remains the owner of product taxonomy/schema. Seller `business_category` is storefront/verification metadata and does not transfer Catalog taxonomy ownership to Sellers.

## API contract

Public v1 endpoints are intentionally small:

- `GET list_sellers`: strict filters, maximum 50 rows, opaque keyset cursor, optional global WGS84 proximity filter.
- `GET get_seller`: canonical `seller_id` only.
- `GET list_seller_map_points`: validated bounded viewport returning pins/clusters.
- `GET get_seller_location`: canonical `seller_id` for public reads, or no ID for the authenticated owner's location.
- `GET get_my_seller_status`: authenticated owner status/capabilities.
- `POST update_my_seller`: strict storefront schema and optimistic `expected_version`.
- `POST set_my_seller_location`: explicit owner save with Maps validation/geocoding and optimistic versioning.
- `POST remove_my_seller_location`: explicit owner removal with optimistic versioning.

Read aliases, GET/POST compatibility, email/internal-name Seller references, banner aliases, offset pagination, and deprecated helper endpoints are not part of the current contract. Internal lifecycle/reconciliation helpers are not public APIs.

Responses use the shared AOS envelope and stable public error codes. Raw exceptions, SQL, provider hostnames/names, filesystem paths, DocType internals, and credentials are never returned.

## Concurrency and idempotency

One Seller per Account and public ID uniqueness are enforced by database constraints. Creation handles duplicate-insert races by reloading the winning Seller. Storefront and location writes use row locks plus monotonic versions. Identical mutation retries are no-ops even if the supplied version is stale; conflicting state changes return deterministic 409 errors. Lifecycle transitions are row-locked and illegal transitions fail. Media attach/release participates in the Seller request savepoint/callback boundary so a rejected mutation cannot leave queued side effects from the failed operation.

## Scale, pagination and indexing

Public Seller discovery is stateless and bounded. It uses parameterized SQL, stable keyset pagination, selective joins, batched Account/Media/Social display projection, and an `N+1`-free friend-count aggregate. Nearby discovery uses a WGS84 bounding-box prefilter followed by Haversine distance and handles antimeridian/polar cases. Map viewports are bounded by Maps validation and raw/item caps. Current Seller schema installers reassert composite indexes after DocType synchronization.

No correctness depends on process-local locks, caches, sticky sessions, or a single Frappe replica. Database locking/constraints are authoritative across replicas.

## Caching

Seller does not maintain an authorization-sensitive shared response cache in this phase. Public Media URLs and Maps operations use their hardened subsystem caches. This avoids mixing owner/private, block-dependent, verification, lifecycle, and exact-location responses in a shared Seller cache. Any future Seller cache must use bounded TTLs and deterministic invalidation scoped to public data only.

## Rate limits and abuse protection

All public Seller endpoints are registered in the shared deployment rate-limit manifest. High-value mutations use both authenticated-user and IP dimensions; public discovery/detail/location reads are IP limited and authenticated reads also use a user dimension where applicable. Sellers uses the shared limiter only; there is no Seller-specific authoritative in-process limiter.

## Security and privacy

The design explicitly covers IDOR/ownership checks, opaque identity, block-aware public reads, mass-assignment rejection, parameterized SQL, bounded filters/cursors/payloads, plain-text XSS controls, Media authorization, no user-supplied remote URLs, no Seller-side SSRF providers, Verification separation, optimistic concurrency, deterministic lifecycle rules, and sanitized dependency failures. Location logging excludes coordinates/address text. Verification evidence, tokens, secrets, and private contact/business data are never Seller telemetry.

## Observability and failure behavior

Seller API operations emit structured `aos.sellers` telemetry containing operation, outcome/failure class, bounded latency, optional opaque hashed Seller reference, lifecycle status, and bounded counts. Logs never contain precise coordinates, full address/storefront text, authentication tokens, verification documents, or private contacts. Maps/Media/Notifications retain their own dependency telemetry.

External geocoder failures are mapped to the hardened Maps dependency error; Media failures use Media-safe public codes; unexpected failures return `INTERNAL_ERROR` and are logged server-side. Notification delivery failure does not roll back Seller lifecycle state under the Notifications contract.

## Fresh-site migration and deployment

AOS is installed on a fresh site. Historical Seller data-migration patches, deterministic IDs, operating-day conversion, legacy URL fields, compatibility wrappers, and old Seller-location-in-Maps persistence are removed. `aos.migrate.after_migrate` runs the current idempotent Seller schema/index installer after DocType sync; it does not replay historical data migration.

Deployment must provide the already-hardened Auth/Accounts/Media/Maps/Verification/Notifications/Social/Catalog dependencies and production database/shared rate-limit infrastructure. Seller has no extra single-node service requirement.

## Testing and release gate

Backend tests cover strict validation, opaque IDs, creation races/constraints, owner/non-owner boundaries, Media ownership, public/private serialization, global/antimeridian/high-latitude location behavior, location optimistic concurrency/idempotency, lifecycle transitions, verification projection, notification isolation, pagination/index/static invariants, malformed identifiers, mass assignment, dependency sanitization, and Seller-to-Maps routing lookup.

Sellers is not marked production-ready until backend CI, client Postman validation, web CI, and a real `valid Seller location -> Maps refresh_route -> 200` staging test all pass. Postman and web changes are separate phases.

## Future-feature boundary

Ads, Reviews, Search Ranking, Wishlist, Shorts, Live, Call, Chat, Activity, Reports and Moderation may consume the opaque Seller identity, lifecycle/capabilities, public profile, or route-destination lookup later. Their business rules do not belong in Seller APIs. Existing aggregate hooks required by current code remain server-controlled, but Seller public contracts do not expose future Chat/Live state.
