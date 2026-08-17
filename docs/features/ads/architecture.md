# Ads architecture

## Domain boundaries

Ads does not accept client-supplied owner, seller, country, currency, status, moderation state, counters, ranking values, or audit metadata. The authenticated session resolves the user. The Seller record resolves ownership and eligibility. User preferences and the market-context service resolve country and currency. The selected Location must be active and belong to the authoritative country.

Catalog is called through its public service. The final category must be an active sellable leaf under active ancestors. Resolved attributes define required fields, types, options, service pricing requirements, and allowed units. Ads stores normalized values plus immutable display snapshots; it does not store client-supplied schema metadata.

Media IDs must already have completed the shared Media upload and confirmation pipeline. Ads validates purpose and ownership, attaches after the Ad exists, releases removed relationships on replacement, and releases all related Media on soft deletion. Storage credentials, object keys, and private URLs are never serialized by Ads.

## Transaction boundaries

API implementations do not commit successful business mutations. Frappe's outer request controls durability. A failed response after a partial aggregate mutation explicitly rolls the request transaction back. Scheduled expiry also performs no hidden commit; the scheduler/worker controls transaction durability.

Creation and full resubmission save the aggregate, attach Media, and create the moderation job in the same request transaction. User-driven lifecycle changes lock the Ad before re-reading and applying the transition. The scheduler-owned expiry path also locks/rechecks the Ad but uses a narrow trusted lifecycle mutation instead of replaying mutable-content/market validation against historical Ads. Moderation locks and rechecks the current state before applying a callback decision. Wishlist and report creation use logical-pair locks plus database uniqueness as the final race guard.

External discovery updates use the existing durable companion integrations. The Ads layer asks both image-search and search-ranking foundations to refresh after authoritative state changes. Those foundations retain their existing outbox, generation, callback, retry, and stale-result protections.

## Public eligibility

Every public path enforces:

1. Ad status is `Active`.
2. Seller status is `Active`.
3. `expires_on` is absent or not before the current server date.
4. Authenticated viewers do not have an active block relationship with the seller user.

This boundary applies to list, detail, wishlist listing, and image-based search. Search candidates are treated only as candidate IDs; authoritative database eligibility is always rechecked before serialization.

## Serialization

List, detail, owner-management, and wishlist serializers are explicit. They expose public seller identity, public Media URLs, original price/currency, display price/currency, conversion availability and rate metadata, and authenticated viewer state where applicable. They do not expose owner email, internal moderation notes, storage paths, tokens, raw job payloads, or exception details.

Existing public `id`, seller identifier, Media identifier, and legacy price fields remain for frontend compatibility. Additive `original_*` and `display_*` fields clarify monetary semantics without silently changing existing values.
