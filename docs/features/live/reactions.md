# Live Reactions

## Supported types

- `like`
- `fire`
- `clap`
- `love`
- `wow`

## High-volume model

Reactions require an authenticated participant. The host may react without a viewer row; a non-host must own the active viewer session and pass the canonical account/block policy. Ended Lives reject reactions.

New reaction taps are **Redis-first**. A tap increments an atomic per-Live accumulator and returns the same public reaction payload as before. A viral stream therefore does not create one MariaDB row or one parent-row counter update per tap. `AOS Live Stream.reaction_count` is a materialized aggregate refreshed by the trailing viewer-counter worker and the recurring Live reconciler, then finalized exactly when the Live ends. Existing `AOS Live Stream Reaction` rows remain supported as a degradation/legacy source.

Realtime animation is intentionally lossy under extreme load while counting is not: every accepted tap is counted, but at most 20 reaction animation events per second per Live are published to the room. This keeps heart/fire/clap UI responsive without allowing one room to create unbounded websocket amplification.

The participant request takes a shared Live lifecycle lock and a shared relationship lock. `end_live`, block/unblock, and other incompatible mutations retain exclusive locks and therefore wait for already-accepted participant decisions without serializing viewers against each other.

The endpoint remains limited to 600 attempts per authenticated user per minute. Payloads expose public account display data, not email/internal User IDs.

## Recovery

If Frappe Redis is unavailable, the endpoint falls back to the legacy durable reaction row. Periodic reconciliation derives the correct aggregate from Redis when present or legacy rows otherwise. Terminal finalization materializes the last total and clears the temporary Redis keys.
