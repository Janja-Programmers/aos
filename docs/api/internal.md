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
- [Video processing](../features/shorts/README.md)

## LiveKit webhook

`aos.api.v1.livekit.handle_webhook` is provider-facing. It verifies the LiveKit webhook contract and durable replay/dedupe behavior described in [LiveKit integration](../features/live/livekit.md).

## Admin diagnostics

`aos.api.v1.diagnostics.*` requires effective Read permission on `AOS Settings` even though it uses normal whitelisted transport. See [Diagnostics API](../features/diagnostics/api.md).

## Private metrics

These three unversioned methods exist only for private monitoring:

- `aos.api.metrics.prometheus`
- `aos.api.metrics.background_jobs`
- `aos.api.metrics.backup_readiness`

They are guest-decorated at the Frappe layer so Prometheus can scrape them, but `_serve()` calls `metrics_access_allowed()` before returning any payload. They are **not** part of the public v1 client contract.
