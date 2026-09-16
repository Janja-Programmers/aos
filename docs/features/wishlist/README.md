# Wishlist

Wishlist is the private authenticated relationship between one current AOS user and one canonical Ad. It owns relationship state and the authoritative popularity count feeding Marketplace Discovery. It does not own Ad visibility, Ad card serialization, Media, Seller identity, verification, localization, Maps, or ranking policy.

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `add_to_wishlist` | POST | Session required | Client |
| `list_wishlist` | GET | Session required | Client |
| `remove_from_wishlist` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Responsibilities and boundaries

The authenticated session is the only source of Wishlist ownership. Client payloads never accept `user`, `email`, `owner`, or account identity. Public mutation requests accept only the hardened Ads `ad_id`, which is the opaque `AOS Ad.public_id`; internal Frappe `AOS Ad.name` remains an implementation detail.

Wishlist consumes the canonical Ads visibility boundary for add eligibility and the canonical Marketplace Discovery Ad-card projection for list hydration. That projection remains responsible for public Ad state, seller/account eligibility, block policy, expiry, Media URLs, prices/currency conversion, location labels, and list-card serialization. Search Ranking consumes `AOS Ad.wishlist_count`; Wishlist does not calculate ranking scores.

## Data model

`AOS Wishlist` stores one durable logical row per `(user, ad)` pair. `ad` is an internal Link to `AOS Ad`; `status` is `Active` or `Removed`; `saved_on` and `removed_on` track lifecycle timestamps. Removed rows are retained so repeated add/remove requests converge on one durable relationship instead of creating duplicate history rows.

The current schema installs these database invariants/indexes:

- `uq_aos_wishlist_user_ad (user, ad)` — authoritative duplicate guard.
- `idx_aos_wishlist_user_status_saved (user, status, saved_on, name)` — private recent listing/cursor path.
- `idx_aos_wishlist_ad_status (ad, status)` — count/reconciliation path.

The DocType also uses deterministic hash naming, but correctness does not depend on naming alone. Wishlist is not web-indexed and grants no ordinary end-user Desk permissions; client access is only through the authenticated API boundary.

## Add contract

`POST /api/method/aos.api.v1.wishlist.add_to_wishlist`

Accepted client field:

```json
{
  "ad_id": "ad_<opaque-public-id>"
}
```

The operation converges on `wishlisted=true`. Repeating the same request does not create another row or increment the count again. Adds require the Ad to pass the same current public eligibility boundary used by Ad detail, and sellers cannot wishlist their own Ads.

Successful response data:

```json
{
  "ad_id": "ad_<opaque-public-id>",
  "wishlisted": true,
  "changed": true,
  "wishlist_count": 12
}
```

`changed=false` means the requested state already held.

## Remove contract

`POST /api/method/aos.api.v1.wishlist.remove_from_wishlist`

Accepted client field:

```json
{
  "ad_id": "ad_<opaque-public-id>"
}
```

The operation converges on `wishlisted=false`. Repeating removal is successful with `changed=false`. A retained relationship authorizes its owner to clear or retry removal even after the Ad becomes Sold, Reviewing, Declined, Expired, Deleted, Suspended, blocked, or otherwise unavailable. If the current user has never had that relationship, removal requires the normal Ads visibility boundary; it therefore cannot be used to confirm that a hidden/moderated Ad exists.

## List contract and pagination

`GET /api/method/aos.api.v1.wishlist.list_wishlist`

Accepted fields are only:

- `limit` — bounded by the Ads page-size ceiling.
- `cursor` — opaque recent-order cursor.
- `country`, `currency` — display/market context consumed by canonical Ads projection.

Wishlist-specific search, seller/category filters, price filters, rating filters, promotion filters, sort aliases, and offset pagination are not part of the current contract. Those belong to Marketplace Discovery rather than the relationship domain.

Relationships are scanned in deterministic `saved_on DESC, wishlist name DESC` order. The scan has bounded headroom so stale hidden relationships do not cause unbounded work. Public card hydration is then delegated in one bounded batch to `load_public_ad_items`; unavailable Ads are omitted without exposing the reason. Returned cards always have `is_wishlisted=true` and include `wishlisted_on`.

## Ad lifecycle and deletion

Changing an Ad away from the canonical public state does not rewrite the private Wishlist row. The list projection simply stops returning that Ad, while `remove_from_wishlist` remains available. This avoids leaking moderation, seller-account, block, or private lifecycle details.

Normal Ads deletion is a lifecycle state (`Deleted`), so retained Wishlist rows remain valid Links and are hidden by Ads visibility. Exceptional hard deletion remains subject to Frappe Link integrity, preventing harmful Wishlist orphans. Search Ranking already handles Ad index deletion through the Ads aggregate lifecycle.

## Count authority and Search Ranking

Active `AOS Wishlist` rows are the relationship truth. `AOS Ad.wishlist_count` is an intentionally maintained transactional derived counter used by Marketplace Discovery/Search Ranking. DocType insert/update/trash hooks adjust it atomically with relationship state changes. Permanent account purge is bounded and recomputes affected Ad counters after its controlled raw cleanup.

Only real state transitions change the count or request a Search Ranking refresh. Duplicate add/remove retries do neither. Search Ranking refresh creation uses the existing durable search-index/outbox architecture; downstream ranking failures are logged and must not corrupt a successful Wishlist transaction.

## Concurrency and transactions

No Python globals, process-local locks, or single-worker assumptions participate in correctness. Existing relationship updates take a database row lock. First-insert races are arbitrated by database uniqueness; a losing duplicate insert rolls back to a local savepoint, locks the winner, and converges on the requested Active state.

Add and remove are explicit idempotent commands. Truly simultaneous opposing commands are serialized by database state where a row exists; clients should also serialize UI mutations per Ad. Every reachable committed state remains valid, unique, and count-consistent.

Wishlist endpoints never call `frappe.db.commit()` in normal request-domain logic. Mutation failures roll back to the endpoint-local savepoint. Activity Center updates are scheduled after commit and are best-effort. Wishlist currently emits no seller notification for add/remove.

## Security and abuse controls

All endpoints require the hardened authenticated account boundary. Ownership comes from the session. The API never lists or mutates another user's Wishlist. Hidden Ads are omitted from listing; add fails through the canonical Ads not-found boundary; and remove only bypasses public visibility when the authenticated owner already has the retained private relationship. Add/remove share one hashed per-user mutation rate-limit bucket; listing has its own bounded read rate limit.

## Frontend usage

The UI may present one heart/save control, but transport calls are explicit: unsaved → `add_to_wishlist`; saved → `remove_from_wishlist`. Ads list/detail projections already provide authenticated Wishlist state in bounded batches/single-Ad reads; clients should not issue one Wishlist-state request per card. Optimistic UI must roll back on mutation failure and update the shared Ad cache consistently.

## Removed legacy behavior

The current fresh-site architecture has no `toggle_wishlist` endpoint, alias, wrapper, optional `wishlisted` mutation flag, `id`/`listing_id`/`item_id`/`product_id` Ad aliases, historical Wishlist backfill patch, Wishlist-specific Ad serializer, or old image URL fallback. The old historical hardening migration was replaced by the current schema-only Wishlist index installer.

## Validation

The backend suite covers authenticated add/remove, guest rejection, owner isolation, duplicate retries, unavailable Ad removal, canonical public identifiers, hidden/moderation-blocked listing behavior, cursor validation/order, exact derived counts, ranking side-effect failure safety, API legacy rejection, schema indexes, and static guards against reintroducing the removed transport or duplicated Ads projection logic.
