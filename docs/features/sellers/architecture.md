# Architecture

## Ownership

**Seller owns:** storefront fields, Seller status, Seller type projection, seller capabilities, public Seller serialization, public Seller discovery, and stored seller aggregate fields.

**Accounts owns:** user identity, account lifecycle, public account IDs, names, avatars, privacy state, and follower counts.

**Media owns:** upload validation, object storage, ownership, attachment state, release, and canonical URLs. Seller only requests `seller_banner` attachment.

**Ads owns:** ad lifecycle. Seller consumes the Active-ad count as a projection. Ad transitions update this projection atomically and reconciliation can repair drift.

**Reviews owns:** review lifecycle and approved-review aggregation. Seller exposes the resulting rating/count without recalculating review rules.

**Maps owns:** coordinates, coverage, reverse geocoding, route calculation, map pins, clustering, and geospatial indexes. Seller determines whether an active storefront may publish a location.

**Verification owns:** evidence and approval decisions. An approved business verification calls the public Seller projection service; it does not directly own Seller fields.

## Layers

1. Versioned Frappe wrappers strip framework transport metadata.
2. Thin endpoint implementations apply authentication and operation-specific rate limits.
3. `SellerService` validates requests and orchestrates reads/mutations.
4. Seller policies own lifecycle and capability decisions.
5. serializers expose public-safe projections.
6. DocType controllers enforce persistence invariants even for non-API writes.
