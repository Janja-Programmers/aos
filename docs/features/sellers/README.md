# Sellers

The Seller domain owns marketplace storefront identity, lifecycle, capabilities, public discovery, and seller-owned aggregate projections. It does not own account identity, geospatial processing, verification decisions, media storage, ad lifecycle, or review moderation.

## Core public API

- `list_sellers`
- `get_seller`
- `get_my_seller_status`
- `update_my_seller`

The existing location endpoints remain Seller-facing compatibility contracts while the Maps subsystem continues to own coordinate validation, reverse geocoding, routing, viewport search, and coverage policy.

## Production properties

- Opaque immutable `SELLER-*` public identifiers
- Strict request allowlists and bounded validation
- Active-only public discovery
- Block-, account-state-, and suspension-aware visibility
- Media-backed storefront banners
- Optimistic storefront concurrency
- Explicit Seller lifecycle transitions
- Active-ad and approved-review aggregates
- Bounded pagination, deterministic sorting, rate limits, safe errors, and privacy-safe logs
