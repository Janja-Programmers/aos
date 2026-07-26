# Ads API contract

The stable external boundary remains under `/api/method/aos.api.v1.*`.

## Ads routes

- `POST aos.api.v1.ads.create_ad`
- `GET aos.api.v1.ads.list_ads` (guest allowed)
- `GET/POST aos.api.v1.ads.search_ads_by_image` according to the existing multipart client contract (guest allowed)
- `GET aos.api.v1.ads.get_ad` (guest allowed)
- `GET aos.api.v1.ads.list_my_ads`
- `GET aos.api.v1.ads.get_my_ad`
- `POST aos.api.v1.ads.update_ad`
- `POST aos.api.v1.ads.set_ad_status`
- `POST aos.api.v1.ads.upsert_ad_draft`
- `GET aos.api.v1.ads.list_my_ad_drafts`
- `GET aos.api.v1.ads.get_my_ad_draft`
- `POST aos.api.v1.ads.abandon_ad_draft`
- `POST aos.api.v1.ads.submit_ad_draft`

Related routes:

- `POST aos.api.v1.wishlist.toggle_wishlist`
- `GET aos.api.v1.wishlist.list_wishlist`
- `GET aos.api.v1.reports.list_report_reasons`
- `POST aos.api.v1.reports.report_ad`

## Mutation rules

Mutation endpoints reject unknown fields. Scalars reject arrays/objects, text is Unicode-normalized and bounded, control characters and null bytes are rejected, booleans and integers are strict, and monetary values use Decimal quantization. Ownership and seller identity are always session-derived.

`create_ad` and a full update of Reviewing/Declined Ads require a complete valid payload, one to four confirmed `ad_image` Media IDs, exactly one primary image, all required Catalog attributes, and valid pricing for the resolved category. Active Ads allow only the existing safe edit subset: title, description, and pricing fields. Category, market, attribute, and Media replacement of an Active Ad require an explicit resubmission workflow rather than an unsafe direct public mutation.

`toggle_wishlist` preserves legacy toggle behavior when `wishlisted` is omitted. Sending `wishlisted=1` or `wishlisted=0` gives idempotent desired-state behavior. Duplicate logical rows are prevented by the database.

## Pagination and sorting

Public listing and wishlist listing have a default limit of 20 and maximum of 50. Offset is bounded. Sort and filter fields are allowlisted and all orderings have deterministic tie-breakers.

Public `list_ads` preserves offset pagination. Category, seller, pricing, promotion, rating, and verification filters are applied conjunctively. Country and location remain marketplace ranking context. Explicit `price_low`, `price_high`, and `recent` sorts are the primary ordering when combined with those filters; geographic and verified-seller ranking are deterministic tie-breakers rather than overrides. For `sort=recent`, clients may additionally use the opaque `cursor` returned as `pagination.next_cursor`; cursor mode requires zero offset and no search query. Search-ranking relevance is used for the default best-match ordering while candidates remain bounded and are re-filtered by authoritative public eligibility.

## Errors

Responses use the existing AOS envelope and machine-readable `error` field. Ads-specific errors are mapped centrally to safe 4xx/409 responses. Resource-existence-sensitive authorization failures use the same public not-found message. Raw database, storage, callback, filesystem, and exception details are not returned.
