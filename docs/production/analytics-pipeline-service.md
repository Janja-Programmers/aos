# AOS Analytics Pipeline Service

Phase 5 moves high-volume analytics ingestion out of the Frappe request path.

## Ownership

- Frappe owns users, permissions, feature state, and durable `AOS Analytics Ingest Job` records.
- The external analytics-pipeline service owns fast Redis streams/counters and future aggregation/export work.
- Frappe dispatches signed jobs to the service.
- The service returns signed callbacks to Frappe.

## Containers

- `analytics-redis`
- `analytics-api`
- `analytics-worker`

## Health

```bash
curl http://127.0.0.1:8170/health
curl http://127.0.0.1:8170/ready
```

## Smoke test

```bash
curl -X POST "https://api.example.com/api/method/aos.api.analytics_pipeline.track_event" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"smoke_test","event_group":"system","source":"manual"}'
```

Then inspect `AOS Analytics Ingest Job`. Expected status path:

`Queued -> Dispatching -> Processing -> Ingested`

## Redis counters

The worker writes:

- raw stream: `aos:analytics:events`
- daily counters: `aos:analytics:day:<YYYY-MM-DD>`
- group counters: `aos:analytics:group:<group>:<YYYY-MM-DD>`
- target counters: `aos:analytics:target:<doctype>:<name>:<YYYY-MM-DD>`
- user counters: `aos:analytics:user:<user>:<YYYY-MM-DD>`
