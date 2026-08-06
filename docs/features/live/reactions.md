# Live Reactions

## Supported types

- `like`
- `fire`
- `clap`
- `love`
- `wow`

## Rules

Reactions require an authenticated participant. Host may react without a viewer row; non-host must own the active viewer session and pass canonical block/account policy. Ended Lives reject reactions.

Each accepted reaction uses the established persistent `AOS Live Stream Reaction` event row and emits `aos_live_reaction` after commit. The reaction and Live aggregate counters are synchronized by the established DocType/analytics behavior.

The endpoint is limited to 300 accepted attempts per user per minute in addition to the reviewed edge baseline. Payloads expose public account display data, not email/internal User IDs.

## Product boundary

The current product already persists reaction events, so this hardening preserves that behavior. It does not add per-user history/list endpoints or expose participant reaction histories. Retention/aggregation-window changes require an explicit product migration and are deferred.
