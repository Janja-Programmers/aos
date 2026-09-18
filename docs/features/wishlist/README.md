# Wishlist

## Overview
Wishlist is the private authenticated relationship between one AOS account and one canonical Ad. It owns saved/removed relationship state and the authoritative `AOS Ad.wishlist_count` projection consumed by marketplace ranking/discovery.

## Responsibilities
Wishlist owns add/remove convergence, private list pagination/filtering, relationship uniqueness/history, count updates/reconciliation, authorization and account/ad lifecycle handling.

## Boundaries
Authentication supplies current identity; Accounts resolves account ownership; Ads owns Ad visibility/card serialization; Search Ranking consumes counts; Catalog/Media/Sellers/Localization remain behind the Ads projection. Wishlist does not duplicate their rules.

## Architecture
```text
Versioned Wishlist API -> wishlist service/repository -> AOS Wishlist -> AOS Ad wishlist_count projection
Wishlist listing -> canonical Ads visibility + marketplace card projection
```

## Data Model
- `AOS Wishlist`: deterministic/hash-named durable `(user, ad)` relationship with `Active`/`Removed` state.
- `AOS Ad.wishlist_count`: Ads-owned derived popularity projection maintained from Wishlist mutations/reconciliation.

## Fields
| Model | Field | Required / constraint | Purpose |
|---|---|---|---|
| AOS Wishlist | `user` + `ad` | database unique pair | Canonical relationship identity. |
| AOS Wishlist | `status` | Active/Removed | Convergent lifecycle without duplicate history rows. |
| AOS Wishlist | `saved_on` / `removed_on` | lifecycle timestamps | Stable keyset ordering/audit state. |
| AOS Ad | `wishlist_count` | maintained projection | Fast popularity/ranking input. |

## API
`add_to_wishlist`, `remove_from_wishlist`, and `list_wishlist` accept the current canonical Ads public `ad_id`; ownership always comes from the authenticated session. Mutations are idempotent/convergent and list pagination uses bounded opaque keyset cursors.

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

## Cross-feature Dependencies
Wishlist consumes Authentication/Accounts identity and canonical Ads visibility/card projection. Search Ranking reads the maintained Ads count. Hidden resource existence is not leaked through Wishlist mutation semantics.

## Transaction / Concurrency Model
The database unique `(user, ad)` constraint and deterministic identity prevent duplicate logical relationships. Competing mutations serialize through row/database constraints, update the count only on real state transitions, and rely on normal request transaction rollback on failure.

## Caching
Relationship truth and counts are database-backed. Any Ads/ranking cache is derived and invalidated by the owning feature; Wishlist has no process-local correctness cache.

## Performance / Scalability
User/status/saved and ad/status indexes support bounded private lists and count/reconciliation paths. Add/remove avoid naming-series allocation and duplicate relationship inserts. High-frequency popularity writes remain a potential hot-row workload requiring realistic contention tests.

## Testing
Tests under `aos/api/wishlist/tests` and `aos/tests` cover add/remove idempotency, private listing/filtering, hidden Ad policy, counts, concurrency, account lifecycle and schema indexes. Test fixtures create valid Ads through shared builders and remove Wishlist/Ad dependencies in teardown.

## Detailed Reference

Wishlist is the private authenticated relationship between one current AOS user and one canonical Ad. It owns relationship state and the authoritative popularity count feeding Marketplace Discovery. It does not own Ad visibility, Ad card serialization, Media, Seller identity, verification, localization, Maps, or ranking policy.



### Responsibilities and boundaries

The authenticated session is the only source of Wishlist ownership. Client payloads never accept `user`, `email`, `owner`, or account identity. Public mutation requests accept only the canonical Ads `ad_id`, which is the opaque `AOS Ad.public_id`; internal Frappe `AOS Ad.name` remains an implementation detail.

Wishlist consumes the canonical Ads visibility boundary for add eligibility and the canonical Marketplace Discovery Ad-card projection for list hydration. That projection remains responsible for public Ad state, seller/account eligibility, block policy, expiry, Media URLs, prices/currency conversion, location labels, and list-card serialization. Search Ranking consumes `AOS Ad.wishlist_count`; Wishlist does not calculate ranking scores.

### Data model

`AOS Wishlist` stores one durable logical row per `(user, ad)` pair. `ad` is an internal Link to `AOS Ad`; `status` is `Active` or `Removed`; `saved_on` and `removed_on` track lifecycle timestamps. Removed rows are retained so repeated add/remove requests converge on one durable relationship instead of creating duplicate history rows.

The current schema installs these database invariants/indexes:

- `uq_aos_wishlist_user_ad (user, ad)` — authoritative duplicate guard.
- `idx_aos_wishlist_user_status_saved (user, status, saved_on, name)` — private recent listing/cursor path.
- `idx_aos_wishlist_ad_status (ad, status)` — count/reconciliation path.

The DocType also uses deterministic hash naming, but correctness does not depend on naming alone. Wishlist is not web-indexed and grants no ordinary end-user Desk permissions; client access is only through the authenticated API boundary.

### Add contract

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

### Remove contract

`POST /api/method/aos.api.v1.wishlist.remove_from_wishlist`

Accepted client field:

```json
{
  "ad_id": "ad_<opaque-public-id>"
}
```

The operation converges on `wishlisted=false`. Repeating removal is successful with `changed=false`. A retained relationship authorizes its owner to clear or retry removal even after the Ad becomes Sold, Reviewing, Declined, Expired, Deleted, Suspended, blocked, or otherwise unavailable. If the current user has never had that relationship, removal requires the normal Ads visibility boundary; it therefore cannot be used to confirm that a hidden/moderated Ad exists.

### List contract and pagination

`GET /api/method/aos.api.v1.wishlist.list_wishlist`

Accepted fields are:

- `limit` — bounded by the Ads page-size ceiling.
- `cursor` — opaque keyset cursor bound to the active search/filter/sort scope.
- `country`, `currency` — display/market context consumed by canonical Ads projection.
- `q` — server-side search within the authenticated user's saved Ads (title/description; minimum two characters).
- `sort` — one of `saved_recent` (default), `saved_oldest`, `price_low`, `price_high`, `rating_high`.
- `category` — canonical Catalog category filter, including the same category-resolution semantics used by Ads discovery.
- `location` — canonical Ads/Maps location identifier.
- `seller` — canonical public Seller identifier.
- `price_type` — canonical Ads price type.
- `price_min`, `price_max` — display-currency price range; cross-currency filtering fails closed when a current conversion is unavailable.
- `rating_min` — minimum current Ad rating from 0 through 5.
- `verified_seller` — `0`/`1`; when true only Ads whose canonical seller profile is verified are returned.

The list contract accepts only Wishlist-owned filters, cursor pagination, and canonical public Ad identifiers; generic Marketplace Discovery ranking controls are outside this boundary. Search/filter/sort are scoped to the authenticated user's Wishlist membership and do not turn Wishlist into a second Ads API.

Pagination is deterministic keyset pagination for every supported sort. The cursor includes a hash of the active market/search/filter/sort scope, so a cursor from one query cannot be reused after changing filters or ordering. The relationship/eligibility query selects only the bounded page plus one row; public card hydration is then delegated in one bounded batch to `load_public_ad_items`. Ads remains authoritative for card serialization, Media, seller/account eligibility re-checks, currency presentation, and visibility. Returned cards always have `is_wishlisted=true` and include `wishlisted_on`.

### Ad lifecycle and deletion

Changing an Ad away from the canonical public state does not rewrite the private Wishlist row. The list projection simply stops returning that Ad, while `remove_from_wishlist` remains available. This avoids leaking moderation, seller-account, block, or private lifecycle details.

Normal Ads deletion is a lifecycle state (`Deleted`), so retained Wishlist rows remain valid Links and are hidden by Ads visibility. Exceptional hard deletion remains subject to Frappe Link integrity, preventing harmful Wishlist orphans. Search Ranking already handles Ad index deletion through the Ads aggregate lifecycle.

### Count authority and Search Ranking

Active `AOS Wishlist` rows are the relationship truth. `AOS Ad.wishlist_count` is an intentionally maintained transactional derived counter used by Marketplace Discovery/Search Ranking. DocType insert/update/trash hooks adjust it atomically with relationship state changes. Permanent account purge is bounded and recomputes affected Ad counters after its controlled raw cleanup.

Only real state transitions change the count or request a Search Ranking refresh. Duplicate add/remove retries do neither. Search Ranking refresh creation uses the existing durable search-index/outbox architecture; downstream ranking failures are logged and must not corrupt a successful Wishlist transaction.

### Concurrency and transactions

No Python globals, process-local locks, or single-worker assumptions participate in correctness. Existing relationship updates take a database row lock. First-insert races are arbitrated by database uniqueness; a losing duplicate insert rolls back to a local savepoint, locks the winner, and converges on the requested Active state.

Add and remove are explicit idempotent commands. Truly simultaneous opposing commands are serialized by database state where a row exists; clients should also serialize UI mutations per Ad. Every reachable committed state remains valid, unique, and count-consistent.

Wishlist endpoints never call `frappe.db.commit()` in normal request-domain logic. Mutation failures roll back to the endpoint-local savepoint. Activity Center updates are scheduled after commit and are best-effort. Wishlist currently emits no seller notification for add/remove.

### Security and abuse controls

All endpoints require the canonical authenticated account boundary. Ownership comes from the session. The API never lists or mutates another user's Wishlist. Hidden Ads are omitted from listing; add fails through the canonical Ads not-found boundary; and remove only bypasses public visibility when the authenticated owner already has the retained private relationship. Add/remove share one hashed per-user mutation rate-limit bucket; listing has its own bounded read rate limit.

### Frontend usage

The UI may present one heart/save control, but transport calls are explicit: unsaved → `add_to_wishlist`; saved → `remove_from_wishlist`. Ads list/detail projections already provide authenticated Wishlist state in bounded batches/single-Ad reads; clients should not issue one Wishlist-state request per card. Optimistic UI must roll back on mutation failure and update the shared Ad cache consistently.

### Validation

The backend suite covers authenticated add/remove, guest rejection, owner isolation, duplicate retries, unavailable Ad removal, canonical public identifiers, hidden/moderation-blocked listing behavior, Wishlist-scoped search and filters, all supported sort/cursor paths, query-bound cursor rejection, exact derived counts, ranking side-effect failure safety, schema indexes, and the shared Ads card projection boundary.
