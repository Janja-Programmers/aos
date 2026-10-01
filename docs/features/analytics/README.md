# Analytics

## Overview

Analytics measures AOS product behavior without owning product business state. The shared Analytics pipeline is a private server-to-service path for bounded server-authoritative telemetry. Client-observed telemetry remains behind feature APIs when feature state is required to validate the observation; Shorts is the current example.

## Responsibilities

Analytics owns telemetry contracts, bounded analytical counters, rollups/projections, dedupe primitives, retention and privacy rules for analytical data. Owning domains remain authoritative for Ads, Shorts, Live, Accounts, Sellers, Reviews, Activity, Reports and Moderation.

## Boundaries

`Activity` is durable user-facing history. `Analytics` is product telemetry and aggregate measurement. `Diagnostics` is operational health/failure telemetry. Analytics must not copy private Chat bodies, Call contents, Report details, Moderation evidence, raw private content, email addresses, phone numbers or precise location histories.

There is no generic public Analytics ingestion API. Arbitrary client event names and arbitrary analytics metadata are not an AOS contract. Feature-specific client telemetry is accepted only where the owning feature can validate visibility and semantics. The analytics companion callback is private and HMAC-authenticated even though Frappe exposes it with `allow_guest=True` to permit service authentication.

## Architecture

Server-authoritative path:

`feature transaction/request -> canonical event adapter -> AOS Analytics Ingest Job -> transactional outbox -> signed analytics-api /events -> RQ worker -> Redis stream/counters -> signed callback -> durable job status`

Client-observed Shorts path:

`Shorts client -> Shorts record_events/record_share -> Shorts validation + durable semantic rows / Redis hot metrics -> Shorts daily rollup`

The shared companion consists of `analytics-api`, `analytics-worker`, and persistent AOF-backed `analytics-redis`. Analytics failures are fail-open for non-critical product behavior; invalid taxonomy/payloads are programmer/contract errors and are rejected rather than silently reinterpreted.

## Data Model

`AOS Analytics Ingest Job` is a durable orchestration/audit record, not the analytics warehouse. It uses hash naming (no naming-series hot spot), stores only an opaque `actor_account_id` (`ACC-*`) rather than Frappe User/email identity, records the canonical target, bounded event JSON, dispatch status and callback counts. Generic service-job cleanup prunes old payload bodies; optional configured deletion can remove old terminal job rows.

`AOS Short Event`, `AOS Short View`, `AOS Short Metrics Daily`, Shorts Redis hot metrics, and Live metrics remain feature-owned because their semantics depend on feature state. They are not duplicated into the generic pipeline merely for centralization.

## Fields

The shared event envelope is fixed: `event_id`, `event_type`, `event_group`, `actor_account_id`, `source`, `platform`, `country`, `target_doctype`, `target_name`, `route_type`, `route_id`, `occurred_at`, `metadata`, and `metrics`. Producers cannot choose server-owned group/doctype/route fields; canonicalization derives them from the event taxonomy. Event metadata/metrics are event-specific and must stay bounded/minimal.

## Canonical event taxonomy

| Event | Producer | Authority | Actor | Target | Dedupe | Privacy |
|---|---|---|---|---|---|---|
| `ad_detail_view` | Ads `get_ad` | Server-authoritative observation that an authorized ad-detail request succeeded | optional `ACC-*` | `AD-*` / `AOS Ad` | work retry; explicit event ID when supplied | opaque account ID only |
| Shorts `impression`, `playback_start`, `qualified_view`, `watch`, `complete`, `rewatch`, `early_skip`, `follow_from_content` | Shorts `record_events` | Client-observed, server-validated | internal feature identity/session | `SHR-*` | client event ID + actor/session + short | feature-owned; bounded |
| Shorts `share`, `download` | Shorts dedicated endpoints | client action with server validation | internal feature identity/session | `SHR-*` | required idempotency/event identity | feature-owned; bounded |
| Live view/watch metrics | LiveKit webhook / Live services | server/service-authoritative | participant/session semantics owned by Live | `LIVE-*` | Live webhook/session rules | feature-owned aggregate state |

No alias taxonomy is retained in the shared pipeline. Activity's `ad_view` is intentionally a different user-facing Activity type and is not an Analytics event alias.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `handle_callback` | POST | Guest allowed | Signed callback |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

Public Analytics API: **NOT APPLICABLE**. There is no generic `track_event` or `track_events` route.

Internal service callback: `POST /api/method/aos.api.v1.analytics_pipeline.handle_callback`. It requires the callback timestamp/signature contract and is not a public client ingestion endpoint.

The analytics companion exposes private infrastructure endpoints `/events`, `/health`, `/ready`, internal job-status/replay routes, and private operational metrics. They are network/service interfaces, not AOS customer APIs.

## Cross-feature Dependencies

Ads emits the current shared canonical server event. Shorts and Live keep feature-specific analytics implementations because they enforce domain semantics. Accounts supplies canonical `ACC-*` identity. Transactional outbox supplies durable dispatch/reconciliation. Operational metrics/Diagnostics observe the pipeline but are not product Analytics.

## Transaction / Concurrency Model

A shared pipeline job and its transactional outbox record are created in Frappe so rolled-back transactions do not become independently dispatched analytical truth. Dispatch uses stable job/outbox identity. The companion dedupes atomically in Redis before stream/counter mutation. Explicit event IDs dedupe across jobs; otherwise identity is stable for retries of the same work item. Legitimate repeated events without the same identity remain countable.

The companion updates separate day/group/country/actor/target hashes rather than a popular Ads row on every event. Redis Lua performs dedupe, stream append and counter increments atomically. Feature-specific Shorts hot counters likewise avoid per-heartbeat Short-row writes.

## Caching

Redis is shared infrastructure, never process-local correctness state. The analytics stream is capped by `ANALYTICS_STREAM_MAX_LEN`; event dedupe keys expire via `ANALYTICS_EVENT_DEDUPE_TTL_SECONDS`; daily aggregate hashes expire via `ANALYTICS_AGGREGATE_RETENTION_SECONDS` (default 400 days). Redis uses AOF persistence and `noeviction`; capacity/alerting must therefore be sized and tested deliberately.

## Retention

Default shared-pipeline retention: event dedupe keys 30 days; aggregate hashes 400 days; raw stream bounded by maximum length rather than unbounded growth; durable companion results 7 days; Frappe successful payload bodies 30 days and failed payload bodies 90 days via service-job cleanup. Full Frappe job deletion is configurable and disabled by default for auditability. Long-term product aggregates should be materialized deliberately before expiring raw/high-volume telemetry; Redis must not be treated as an unlimited warehouse.

## Performance / Scalability

The request path does not synchronously write every event into a central MariaDB raw-event table. Durable server events are queued through the transactional outbox and processed by horizontally scalable RQ workers. High-frequency Shorts playback is handled by its feature-specific batch endpoint/hot counters. Popular targets use partitioned Redis hashes instead of updating one content row per observation.

The design removes obvious single-process and hot-row correctness dependencies, but **1 million-user capacity is not proven**. Load testing must establish sustained/peak events per second, Redis memory growth under the configured stream/retention, RQ queue latency/backpressure, callback throughput, Frappe outbox/job growth, Shorts hot-metric flush throughput, Live concurrency, and dashboard/query latency for any future analytical read surface.

## Failure Handling

Non-critical Analytics failures do not make Ads/Shorts/Live unavailable. Signed companion dispatch/callback, durable result state, retry/reconciliation, and idempotent work identity isolate transient failures. Unknown canonical events are rejected. Operational failures belong in Diagnostics/metrics/logging rather than becoming product telemetry dimensions.

## Authorization and Privacy

Clients cannot set server-authoritative actor identity or emit shared server events. The shared pipeline stores only canonical opaque account IDs, never Frappe User/email identity. Targets are canonical feature IDs. Seller/customer analytical query APIs do not currently exist, so there is no private dashboard IDOR surface in Analytics. Any future query surface must enforce ownership server-side and use bounded date/resource/dimension/page contracts.

## Testing

Companion tests cover HMAC authentication, schema/count bounds, queue failure behavior, effectively-once ingestion, explicit-event dedupe, durable lifecycle/callback replay, Redis/RQ lifecycle and redacted operational metrics. Frappe tests cover event-to-outbox callback behavior and architecture/source guards. Feature suites cover Shorts and Live semantic analytics independently.

Tests must use rollback or explicit cleanup for every created record/cache key and remain order-independent. No million-row unit tests are used as a substitute for load testing.

## Operations / acceptance

Health: `GET http://127.0.0.1:8170/health`; readiness: `GET http://127.0.0.1:8170/ready`. For a real server smoke test, exercise a normal visible Ad detail request, then verify the resulting `AOS Analytics Ingest Job` progresses `Queued -> Dispatching -> Processing -> Ingested` and that analytics Redis contains `ad_detail_view` counters. Do not create synthetic client events through a public endpoint.

Postman: **NOT APPLICABLE** for Analytics itself. Customer-facing Analytics web: **NOT APPLICABLE**; no seller/creator/customer Analytics dashboard exists in this backend contract.
