# Aggregates

`total_ads` counts only Ads with status `Active`. Ad insert, status transition, seller reassignment, and deletion update the projection atomically with non-negative SQL arithmetic.

Seller rating and `total_reviews` are owned by the Reviews aggregate service and include only approved reviews.

Operational reconciliation:

```bash
bench --site <site> execute aos.services.sellers.aggregates.reconcile_seller_ad_counts --kwargs '{"dry_run": true, "batch_size": 100}'
```

After reviewing drift, rerun with `dry_run: false`. Work is bounded, idempotent, and does not commit inside the service.
