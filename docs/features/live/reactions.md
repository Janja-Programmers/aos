# Live Reactions

## Supported types

- `like`
- `fire`
- `clap`
- `love`
- `wow`

## Rules

Reactions require an authenticated participant. Host may react without a viewer row; non-host must own the active viewer session and pass canonical block/account policy. Ended Lives reject reactions.

Each accepted reaction uses a persistent `AOS Live Stream Reaction` event row and emits `aos_live_reaction` after commit. Reaction writes take a **shared** Live lifecycle lock so rapid taps do not serialize behind an exclusive parent-row lock; start/end and other lifecycle transitions retain the stronger lifecycle lock boundary. The reaction endpoint intentionally does not update the hot `AOS Live Stream.reaction_count` row on every tap. `LiveAnalyticsService.sync_reaction_count()` reconciles the derived aggregate from immutable reaction rows during the established Live reconciliation cycle.

The endpoint is limited to **600 accepted attempts per authenticated user per minute**. This permits normal rapid-tap UI behavior while keeping abuse bounded. Payloads expose public account display data, not email/internal User IDs.

## Product boundary

The current product already persists reaction events, so this hardening preserves that behavior. It does not add per-user history/list endpoints or expose participant reaction histories. Retention/aggregation-window changes require an explicit product migration and are deferred.
