# Internal and Operations HTTP Surfaces

Not every whitelisted method is intended for an end-user client. This page explains the non-client trust boundaries that appear in the complete API reference.

## Signed companion callbacks

The following signed companion callbacks are guest-decorated because private companion services do not hold browser/user Frappe sessions. Some retain historical v1 transport paths owned by their features; Moderation and Video Processing use explicitly internal paths. They authenticate with the AOS callback-signing contract (timestamp plus raw request body/HMAC) and must not be treated as anonymous public APIs:

- `aos.api.v1.analytics_pipeline.handle_callback`
- `aos.api.internal.moderation.handle_callback`
- `aos.api.v1.notifications.handle_delivery_callback`
- `aos.api.v1.search_ranking.handle_callback`
- `aos.api.internal.video_processing.handle_callback`

Operational behavior belongs to the matching production service documentation:

- [Analytics pipeline](../features/analytics/README.md)
- [Content moderation](../features/moderation/README.md)
- [Notifications signed delivery callback](../features/notifications/README.md)
- [Search/ranking](../production/search-ranking-service.md)
- [Video processing](../features/video-processing/README.md)

## LiveKit webhook

`aos.api.v1.livekit.handle_webhook` is provider-facing. It verifies the LiveKit webhook contract and durable replay/dedupe behavior described in [LiveKit integration](../features/livekit/README.md).

## Admin diagnostics

`aos.api.health.liveness` and `aos.api.health.readiness` are minimal infrastructure probes and are not client APIs. Detailed Diagnostics reports are bench/server-side only. See [Diagnostics](../features/diagnostics/README.md).

## Private metrics

These three unversioned methods exist only for private monitoring:

- `aos.api.metrics.prometheus`
- `aos.api.metrics.background_jobs`
- `aos.api.metrics.backup_readiness`

They are guest-decorated at the Frappe layer so Prometheus can scrape them, but `_serve()` calls `metrics_access_allowed()` before returning any payload. They are **not** part of the public v1 client contract.

## Staff-only Desk actions

`aos.api.internal.reports.review` is the only HTTP action used by Report Desk forms to close a complaint. It requires an authenticated staff session, rechecks write permission on the concrete Report DocType, row-locks the record, requires the current optimistic version, and permits only `Reviewing → Resolved|Rejected`. It is not a client API and performs no automated enforcement against the reported target.
