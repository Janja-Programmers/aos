# Live Architecture

## Request path

1. Frappe dispatches a whitelisted method from `aos.api.v1.live`.
2. The wrapper removes only Frappe transport metadata (`cmd`).
3. `aos.services.live.api.run_live_api` validates the strict endpoint contract.
4. Mutation endpoints establish an operation savepoint and snapshot Frappe callback managers and the transactional-outbox registration flag.
5. The established implementation module performs authorization and domain work.
6. Persistent state, activity, notification/outbox, and realtime intents remain in the caller-managed transaction.
7. Realtime uses `after_commit=True`; external LiveKit room work is enqueued with `enqueue_after_commit=True`.
8. On a handled failure, the operation rolls back to its savepoint and restores callback state without rolling back the caller's outer transaction.

## Domain boundaries

### API boundary

- `aos/services/live/endpoints.py`: reviewed fields, aliases, and public ID formats.
- `aos/services/live/validation.py`: scalar normalization, size limits, strict pagination.
- `aos/services/live/api.py`: error normalization, savepoint handling, observability.

### Policy and persistence

- `aos/services/live/policy.py`: account availability and bidirectional block policy through canonical Social services.
- `aos/services/live/repository.py`: bounded lock/read primitives.
- `aos/services/live/cursor.py`: HMAC-signed opaque cursors.

### LiveKit boundary

- `aos/services/live/livekit.py`: opaque identities and privacy-safe metadata.
- `aos/services/livekit_service.py`: signed role-scoped access tokens.
- `aos/services/live/livekit_admin.py`: bounded room/participant admin calls with timeout, retries, and categorized failures.
- `aos/services/live/webhooks.py`: raw-body signature verification, event deduplication, monotonic lifecycle handling.

### Delivery and recovery

- `aos/api/live/realtime.py`: committed room/user events.
- `aos/services/live/notifications.py`: bounded follower fanout through canonical notifications and transactional outbox.
- `aos/services/live/participants.py`: post-commit participant removal intents.
- `aos/tasks/live.py`: room creation/deletion, participant removal, stale-presence repair, account-status recovery, webhook retention.

## Persistent model

| DocType | Responsibility |
|---|---|
| AOS Live Stream | Host ownership, lifecycle, room, cover, counters, cleanup intent |
| AOS Live Stream View | One immutable viewer/session join and its presence/watch duration |
| AOS Live Message | Comments, replies, system/co-host messages, soft deletion |
| AOS Live Stream Reaction | Existing immutable reaction event rows |
| AOS Live CoHost | Invite/request/accept/activate/end workflow |
| AOS LiveKit Webhook Event | Verified event-ID deduplication and processing outcome |

## Transaction and external-consistency model

Database correctness is enforced first. Unique keys are the final concurrency boundaries:

- one active Live per host: `active_host_key`;
- one active viewer session per Live/session: `(live_stream, active_identity_key)`;
- one unresolved co-host workflow per Live/account: `active_workflow_key`;
- one active idempotent comment per Live/author/key: `active_idempotency_key`;
- one webhook event per LiveKit event ID.

LiveKit cannot share the database transaction. The backend therefore records authoritative state, commits it, performs the external operation in a worker, and repairs drift every five minutes.

## Circular-import control

`aos.services.live` has lazy exports. Webhook and task modules use narrow lazy imports only where they need established API helpers after startup. Live serializers, account display helpers, Social policy, and notifications are not eagerly re-exported from the package root.
