# Participants, Presence, and Viewer Metrics

## Roles

The application roles are host, co-host, and viewer. Invitation/request states are workflow states, not token roles. There is no persisted Live moderator role.

- Host ownership is fixed at creation.
- Co-host role requires an accepted workflow, matching candidate, matching immutable session, current Live access, and available slot.
- Viewer role is the default non-host role and is subscribe-only.

## Join and presence

A non-host participant supplies a client-generated session ID. The server derives the participant identity. `track_join` creates or refreshes one active `AOS Live Stream View` for `(live, session)`; database uniqueness resolves concurrent duplicate joins. Repeated join refreshes `last_seen_at` and does not double-count.

`AOS Live Stream View` remains the durable session/watch-history source of truth. High-frequency current-view metrics are maintained atomically in Redis so a join or leave does not scan all historical view rows or update the hot `AOS Live Stream` row while holding a lifecycle lock. A coalesced worker materializes the Redis values to the Live row and publishes a trailing viewer-count event.

The host does not create a view row and is excluded from viewer counts. A co-host continues to use their viewer session and therefore remains included as a current viewer.

`track_leave`, participant-left webhooks, terminal cleanup, and reconciliation close the immutable view row. Closed rows cannot reopen; a later reconnect creates a new session row or refreshes the still-active row.

Host-only `viewer_joined` notifications are ephemeral because the durable view row already records the join. They are sampled to at most 10 targeted events per second per Live during join storms; no public participant identity/session information is broadcast to the room.

## Metric definitions

- **current viewers**: hot Redis count backed by active non-host view sessions.
- **peak viewers**: greatest observed concurrent count; never decreases.
- **unique viewers**: exact distinct authenticated accounts plus distinct guest session IDs after reconciliation/finalization.
- **total joins**: all view-session rows, including reconnect sessions; hot total increments atomically.
- **total views**: backward-compatible alias of total joins.
- **watch time**: exact sum after reconciliation/finalization; hot leave paths add closed-session duration incrementally.

A five-second qualified-view constant remains in the model for product analytics, but the public counters above retain their existing meanings.

## Concurrency boundary

Join, token issuance, tracking, comments, replies, and reactions use shared lifecycle locks. Many participants can therefore make read-side lifecycle decisions concurrently. `end_live` keeps the exclusive lifecycle lock, so it waits for accepted participant transactions and then prevents all new ones.

Live access validation also uses shared locks on the host/viewer account pair. Actual Social relationship mutations keep exclusive locks, preventing all viewers from serializing through the host `tabUser` row.

## Privacy

Public room events contain only aggregate viewer count. No public endpoint exposes an unbounded participant list. Co-host workflow access is limited to the host and candidate. Session IDs and LiveKit identities appear only in targeted internal workflow payloads where the existing mobile contract needs them.

## Reconciliation

Every five minutes the worker compares bounded LiveKit identities with bounded active local rows, repairs stale sessions, materializes hot Redis counters, and performs recovery work. Full historical scans are reserved for reconciliation/terminal finalization rather than every join/leave.
