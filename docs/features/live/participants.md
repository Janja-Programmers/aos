# Participants, Presence, and Viewer Metrics

## Roles

The application roles are host, co-host, and viewer. Invitation/request states are workflow states, not token roles. There is no persisted Live moderator role.

- Host ownership is fixed at creation.
- Co-host role requires an accepted workflow, matching candidate, matching immutable session, current Live access, and available slot.
- Viewer role is the default non-host role and is subscribe-only.

## Join and presence

A non-host participant supplies a client-generated session ID. The server derives the participant identity. `track_join` creates or refreshes one active `AOS Live Stream View` for `(live, session)`; database uniqueness resolves concurrent duplicate joins. Repeated join refreshes `last_seen_at` and does not double-count.

The host does not create a view row and is excluded from viewer counts. A co-host continues to use their viewer session and therefore remains included as a current viewer.

`track_leave`, participant-left webhooks, end-Live cleanup, and reconciliation close the immutable view row. Closed rows cannot reopen; a later reconnect creates a new session row or refreshes the still-active row.

## Metric definitions

- **current viewers**: active non-host `AOS Live Stream View` rows.
- **peak viewers**: greatest observed current-viewer count; never decreases.
- **unique viewers**: distinct authenticated accounts plus distinct guest session IDs over the Live lifetime.
- **total joins**: all view-session rows, including reconnect sessions.
- **total views**: backward-compatible alias of total joins.
- **watch time**: sum of non-negative closed/current view-row duration seconds.

A five-second qualified-view constant remains in the model for product analytics, but the public counters above retain their existing meanings.

## Privacy

Public room events contain only aggregate viewer count. No public endpoint exposes an unbounded participant list. Co-host workflow access is limited to the host and candidate. Session IDs and LiveKit identities appear only in targeted internal workflow payloads where the existing mobile contract needs them.

## Reconciliation

Every five minutes the worker compares up to 2,000 LiveKit identities with at most 2,000 active local rows per Live. A local participant absent from LiveKit is closed only after a two-minute grace. Counts are recalculated from rows, never decremented blindly, so duplicates cannot make counts negative.
## Host co-host selection

A host may select an authenticated viewer for a co-host invitation using the viewer's opaque `aos:participant:*` LiveKit identity. The identity is a room-scoped locator, not an AOS session credential. The server resolves and locks the corresponding active `AOS Live Stream View` row; another viewer's `session_id` is never exposed to the host.

