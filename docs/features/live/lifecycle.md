# Live Lifecycle

## Actual state machine

The repository's authoritative states are:

```text
scheduled -> live -> ended
scheduled --------> ended
ended is terminal
```

`scheduled` is currently an internal creation state used while a host start operation builds the record. There is no public draft, schedule, retry-start, or cancel-before-start API. Failed transactional starts roll back instead of persisting a new public failure state.

## Start

- Requires an authenticated, enabled, active, non-deleted host account.
- Locks the host User row before active-Live lookup/creation.
- Generates the Live public ID and room name server-side.
- Uses `active_host_key` uniqueness as the final concurrent-start boundary.
- A repeated start returns the existing active Live rather than creating a duplicate.
- Cover media is validated through canonical Media ownership/state rules.
- The application Live is committed first; room creation is a post-commit background intent.
- `live_started` activity/realtime/notification work is tied to committed state.

## End

- Locks the Live row and requires immutable host ownership.
- Repeated end is idempotent and returns the ended Live.
- Closes pending, accepted, and active co-host workflows before the Live becomes inactive.
- Closes active viewer sessions through the DocType lifecycle and synchronizes metrics.
- Sets `room_cleanup_pending=1` and schedules room deletion after commit.
- Emits the committed system message and `aos_live_ended` event.

## LiveKit-driven termination

A verified `room_finished` webhook ends an active application Live. It never revives an ended Live. An event older than the application's `started_at` is ignored as stale. Duplicate webhook event IDs are idempotent.

The LiveKit room is configured with a five-minute empty timeout. Therefore host disconnect/reconnect can occur during the server's room lifetime; the backend does not invent a separate disconnected state. Once LiveKit reports the room finished, the Live is terminal.

## Account/moderation availability

Every five minutes reconciliation checks active hosts. If a host becomes disabled, deleted, suspended, or otherwise non-active, the Live is ended, made inaccessible, and its room cleanup is queued. Account deletion performs the same cleanup in the caller-managed deletion transaction.

## Recovery

The reconciliation worker:

- creates a missing room for an application-active Live;
- deletes an ended room with pending cleanup;
- closes stale local presence after a two-minute grace when the participant is absent from LiveKit;
- resynchronizes current/peak/unique/join/watch metrics;
- expires pending co-host workflows;
- ends Lives whose host is no longer available.

External failure never moves `ended` back to `live`. Failed room deletion remains recoverable through `room_cleanup_pending`.
