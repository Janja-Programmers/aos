# Ads security and privacy

## Authorization

Guest access is limited to intentionally public list, detail, and image-search routes. Seller management, drafts, wishlist, reporting, and lifecycle operations require an authenticated session. The seller is resolved from the current user; client-supplied owner or seller fields are rejected.

The aggregate controller is a fail-closed second boundary. Non-privileged writes require the seller user to match the session user. Administrative changes by users with effective `AOS Ad` Write permission still require an explicit lifecycle action. Suspended sellers cannot create, edit, renew, mark available, or otherwise republish Ads. System expiry, suspension, and deletion can still hide resources after seller restriction.

## Input and SQL safety

Mutation fields and query filters use allowlists. Text is normalized, length bounded, and checked for control characters. Structured values are rejected where scalars are required. Dynamic sort expressions are selected only from fixed server-side branches. Search text uses the shared escaped-LIKE helper. SQL values remain bound parameters; the only dynamic identifiers are fixed repository-owned fragments.

## Privacy

Public serializers do not expose seller email, internal owner, private phone data, moderation notes, decline internals beyond the established seller-owned contract, storage paths, private signed credentials, job payloads, or ranking internals. Block relationships hide Ads from authenticated public detail, lists, wishlist results, and image search.

Reports derive `reported_by` from the session, reject self-reporting, require an active reason, and do not expose moderation outcomes through the creation API. Wishlist and report uniqueness prevent duplicate-action amplification.

## Abuse controls

All whitelisted Ads, draft, wishlist, report, and image-search routes remain covered by the central rate-limit validator. Limits execute before expensive external search, Media, or mutation work. Public queries are bounded and deterministic, Media count is capped, draft payloads are capped at 64 KiB, and scheduled expiry runs in batches of 200.
