# API contract

All endpoints use the canonical envelope:

```json
{"ok": true, "message": "...", "data": {}}
```

or:

```json
{"ok": false, "message": "...", "error": "STABLE_CODE", "data": {}}
```

## `list_sellers`

Guest read. Supports bounded `limit`/`offset`, `search`/`q`, `seller_type`, `business_category`/`category`, `is_verified`, `has_location`, country/region/locality, authenticated follow filters, sort, and optional nearby coordinates. Sorts: `recommended`, `nearest`, `rating`, `newest`, `most_ads`, `most_reviewed`.

Public results include only active Sellers backed by active, enabled Accounts. The current user, blocked relationships, deleted/deactivated users, and suspended/deleted Sellers are excluded.

## `get_seller`

Guest read by `seller`, `seller_id`, or `id`. Public and legacy internal references are accepted during migration; only the opaque public Seller ID is returned. Direct lookups do not bypass block or account-state restrictions.

## `get_my_seller_status`

Authenticated read. Returns canonical Seller status and capabilities used by Ads, Verification, and storefront UI. It is the only client-facing source for `can_post_ads`, `can_update_storefront`, `can_manage_location`, and related Seller decisions.

## `update_my_seller`

Authenticated POST. Supports only `business_category`, `about_business`, `operating_hours`, banner Media aliases, `clear_shop_banner`, and `expected_version`. Status, type, metrics, user, location, verification, and lifecycle fields are not mass assignable.
