# AOS Search / Ranking / Recommendation Service

Phase 3 separates fast candidate retrieval and ranking execution from Frappe.

## Ownership boundary

Frappe remains the source of truth for:

- business records
- seller/ad/short visibility
- permissions and audience checks
- final hydration/serialization
- status transitions
- audit/history

The search-ranking service owns:

- Redis-backed searchable indexes
- ad candidate retrieval
- shorts feed candidate retrieval
- simple related-ad recommendations
- ranking-friendly candidate ordering

## Runtime flow

```text
Frappe business event
→ AOS Search Index Job
→ Frappe Redis Queue dispatcher
→ search-ranking-api
→ search-ranking Redis/RQ
→ search-ranking-worker
→ signed callback to Frappe
```

## Services

```text
search-ranking-redis
search-ranking-api
search-ranking-worker
```

## Frappe entry points

- `aos.services.search_ranking_service.enqueue_ad_search_index`
- `aos.services.search_ranking_service.enqueue_short_search_index`
- `aos.api.v1.search_ranking.handle_callback`
- `aos.tasks.search_ranking.dispatch_search_index_job`
- `aos.tasks.search_ranking.refresh_search_indexes`

## Query path

Frappe may ask the service for candidates, but Frappe still hydrates and filters records.

```text
mobile API
→ Frappe endpoint
→ search-ranking service returns IDs/scores
→ Frappe loads authoritative docs
→ Frappe applies permissions/status/market filters
→ Frappe serializes response
```

## Backfill

After first deploy, run:

```bash
bench --site <site> execute aos.tasks.search_ranking.reindex_active_ads
bench --site <site> execute aos.tasks.search_ranking.reindex_visible_shorts
```

Then watch jobs:

```python
frappe.get_all("AOS Search Index Job", fields=["name", "target_doctype", "target_name", "status", "last_error"], order_by="creation desc", limit=20)
```
