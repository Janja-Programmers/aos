# Ads / Listings

The Ads domain orchestrates existing AOS foundations rather than replacing them. Authentication owns identity; Accounts and Sellers own eligibility and market preferences; Catalog owns sellable leaf categories, schemas, pricing requirements, and units; Media owns upload and attachment safety; Localization owns market and display currency; moderation, search ranking, image search, notifications, analytics, and the transactional outbox remain companion foundations.

## Aggregate

`AOS Ad` is the aggregate root. Its owned child rows are `AOS Ad Image` and `AOS Ad Attribute Value`. `AOS Ad Draft` is a private autosave resource rather than a public Ad. `AOS Wishlist` and `AOS Ad Report` are user-action aggregates with database uniqueness on their logical user/Ad pairs.

The persisted seller price and currency are authoritative. Public responses add display-price metadata without mutating the original values. Attribute rows retain a historical key, label, type, and unit snapshot so existing Ads remain readable after Catalog display metadata changes.

## Lifecycle

The repository-established states are:

- `Reviewing`
- `Active`
- `Declined`
- `Sold`
- `Expired`
- `Deleted`
- `Suspended`

Seller actions are centralized in `aos.services.ads.lifecycle`:

- `mark_sold`: `Active -> Sold`
- `mark_available`: `Sold -> Active`
- `renew`: `Expired -> Active`
- `delete`: `Reviewing|Declined|Sold|Expired -> Deleted`

Moderation and system transitions are explicit and require an internal lifecycle action. Repeated `mark_sold`, `mark_available`, and `delete` calls in their completed state are idempotent. Deleted and suspended Ads are terminal for normal mutations. Public discovery requires an Active Ad, Active seller, and a non-expired resource.

## Main modules

- `aos/services/ads/validation.py`: strict normalization, Decimal money, Catalog schema validation, pagination, cursors, drafts, and report text.
- `aos/services/ads/lifecycle.py`: allowed transitions and idempotency.
- `aos/services/ads/authorization.py`: session-derived seller ownership.
- `aos/services/ads/media.py`: canonical Media validation, attachment, replacement, and release.
- `aos/services/ads/indexing.py`: durable discovery refresh through existing image-search and search-ranking integrations.
- `aos/services/ads/mutations.py`: aggregate writes and row locks.
- `aos/services/ads/observability.py`: low-cardinality structured events and redacted operational snapshots.
- `aos/api/ads/*`: thin versioned-API implementations.

See the companion documents in this directory for API, security, migration, operations, and testing details.
