# Wishlist API

Stable public methods:

- `POST aos.api.v1.wishlist.toggle_wishlist`
- `GET aos.api.v1.wishlist.list_wishlist`

All responses use the standard AOS envelope. Clients branch on `error`, never
human-readable `message` text.

## `toggle_wishlist`

Accepted fields:

- `ad_id` or legacy alias `id`
- optional `wishlisted`: boolean-like `1`/`0`, `true`/`false`

When `wishlisted` is supplied, the operation sets the desired state
idempotently. When omitted, legacy toggle behaviour is retained.

Success data:

```json
{
  "ad_id": "AD-2026-00001",
  "wishlisted": true,
  "changed": true,
  "wishlist_count": 12
}
```

`wishlist_count` is returned for successful adds and is `null` for removals so a
stale blocked or moderated Ad does not disclose private aggregate state. Adds
never disclose unavailable ads and return `AD_NOT_FOUND`. Own-ad adds return
`OWN_AD_WISHLIST_FORBIDDEN`.

## `list_wishlist`

The endpoint accepts the same bounded marketplace filters as `list_ads`, plus
optional `cursor`. Default sort is `recent`, based on `saved_on`.

Pagination data is additive and backward compatible:

```json
{
  "limit": 20,
  "offset": 0,
  "returned": 20,
  "has_more": true,
  "next_offset": 20,
  "next_cursor": "opaque-token"
}
```

Cursor pagination requires `sort=recent`, `offset=0`, and no text query. The
first recent page can return `next_cursor`; subsequent requests pass that token.
Offset pagination remains supported for all sorts and filters.

Each item uses the canonical Ads list serializer, always has
`is_wishlisted=true`, and adds `wishlisted_on`.
