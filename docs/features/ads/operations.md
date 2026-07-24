# Ads operations

## Scheduled work

`aos.tasks.ads.expire_ads` runs hourly. It selects at most 200 overdue Active Ads, locks and rechecks each row, applies the explicit `expire` transition, notifies the seller user, and refreshes both discovery integrations. Repeated and overlapping executions are idempotent.

Image-search maintenance now treats non-Active Ads, suspended/missing sellers, expired Ads, and Ads without images as unindexable. Public image-search results independently recheck Ad status, seller status, expiry, and blocks before loading authoritative records.

## Observability

`aos.services.ads.observability.ads_log` emits low-cardinality Ads events using only event, status, operation, outcome, and count. It never labels logs with Ad IDs, users, sellers, titles, search terms, locations, URLs, or tokens.

`ads_operational_snapshot()` provides a redacted Bench-friendly snapshot containing counts by lifecycle status, overdue Active count and age, Reviewing backlog and age, and aggregate Ad moderation job counts. Existing production job monitoring continues to cover moderation, search-ranking, transactional-outbox, queues, and background-job failures.

Example:

```bash
bench --site <site> execute aos.services.ads.observability.ads_operational_snapshot
```

Alert operationally on sustained overdue Active Ads, an aging Reviewing backlog, failed Ad moderation jobs, stale outbox records, failed search-index jobs, or an inactive expiry scheduler. Validate search and image-search cleanup after seller suspension or bulk moderation actions.
