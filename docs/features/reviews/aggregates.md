# Aggregates and reconciliation

`recompute_review_aggregates(ad_id=...)` is the single write path for ad and seller rating/count fields. It calculates from canonical `AOS Review` rows whose status is `Approved` and updates both targets inside the caller's transaction. Review create/edit/withdraw/moderation hooks use target locking, and no aggregate helper commits independently.

Reaction totals are similarly rebuilt through `recompute_review_reaction_counts(review_id=...)`; API mutations, DocType hooks and account-deletion cleanup delegate to that one function.

## Ad reconciliation

```bash
bench --site <site> execute aos.services.reviews.aggregates.reconcile_review_aggregates \
  --kwargs '{"dry_run": true, "batch_size": 100}'
```

Use the returned `next_start_after`:

```bash
bench --site <site> execute aos.services.reviews.aggregates.reconcile_review_aggregates \
  --kwargs '{"dry_run": true, "batch_size": 100, "start_after": "AD-..."}'
```

After reviewing drift, repeat with `dry_run=false`.

## Seller reconciliation

```bash
bench --site <site> execute aos.services.reviews.aggregates.reconcile_seller_review_aggregates \
  --kwargs '{"dry_run": true, "batch_size": 100}'
```

Continue with `start_after`, then repair approved drift with `dry_run=false`.

Both facilities are bounded to 1–500 records per call, are safe to rerun, perform no commit, return opaque target IDs rather than reviewer data, and include zero-review targets so stale non-zero values can be repaired.
