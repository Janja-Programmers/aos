# Operations

## Health signals

Monitor Seller endpoint errors, update conflicts, inactive-seller rejections, Media attachment failures, and aggregate drift. Seller logs use bounded event names and opaque hashed references.

## Common checks

- Public list excludes suspended/deleted sellers.
- `get_my_seller_status` matches Ads posting eligibility.
- Storefront update increments the version once.
- Banner replacement releases the prior Media object.
- Active-ad reconciliation reports no unexpected drift.
- Public outputs contain `SELLER-*`, never an email-shaped Seller name.
