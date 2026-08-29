# Live Operations

## Required services

- Frappe web workers, background workers, scheduler, Redis, and MariaDB. Frappe Redis is required for hot Live counters/rate limits.
- Dedicated LiveKit Redis for room/routing coordination in the self-hosted media stack.
- LiveKit server reachable from clients by WebSocket and from workers by the corresponding HTTP(S) admin endpoint.
- LiveKit API key/secret available only to the backend.
- Notification/outbox worker operational.
- Frappe site `encryption_key` present.

## Scheduler

- Every five minutes: `aos.tasks.live.reconcile_live_state`.
- Daily: `aos.tasks.live.cleanup_live_webhook_events`.
- Transactional outbox publisher continues on its existing one-minute schedule.

## Configuration checklist

1. Configure LiveKit endpoint, API key, and API secret through the repository's existing environment configuration.
2. Set `AOS Settings.livekit_live_token_ttl_minutes` (recommended/default 15; maximum enforced 30).
3. Configure webhook URL and ensure reverse proxy preserves raw body and `Authorization`.
4. Confirm workers can make HTTPS admin requests to LiveKit.
5. Confirm edge/API baseline rate limits include all public methods and do not block the signed webhook.
6. Expose LiveKit RTC TCP `7881/tcp` and UDP mux `7882/udp`; signaling `7880` remains proxied/private.
7. Confirm `livekit-redis` is healthy before LiveKit starts.

## Privacy-safe observability

Logger: `aos.live`.

Fields:

- `operation`;
- `outcome` (`success`, `idempotent`, `duplicate`, `rejected`, `conflict`, `failure`);
- bounded `reason` category;
- `latency_ms`;
- optional bounded `count`.

Never add Live ID, account ID, room name, token, email, comment/search text, raw cursor, IP, or relationship detail to this logger.

Useful operation categories include create/start/end API outcomes, room ensure/cleanup, participant removal, webhook verification/deduplication, notification fanout, and reconciliation. Existing endpoint logs are sanitized fallback error records.

## Recovery commands

```bash
# Reconcile active/ended rooms, presence, counters, host state, and co-host expiry
bench --site <site> execute aos.tasks.live.reconcile_live_state

# Retry one room ensure/delete intent
bench --site <site> execute aos.tasks.live.ensure_live_room --kwargs '{"live_id":"LIVE-2026-00001"}'
bench --site <site> execute aos.tasks.live.cleanup_live_room --kwargs '{"live_id":"LIVE-2026-00001"}'

# Remove old webhook dedupe records
bench --site <site> execute aos.tasks.live.cleanup_live_webhook_events
```

Do not print returned LiveKit tokens or inspect them in shared logs.

## Alerts to add/verify

- room ensure/delete failure ratio and latency category;
- reconciliation partial/failure outcome;
- webhook signature rejects and processing failures;
- duplicate webhook rate anomaly;
- participant-removal failures;
- notification fanout enqueue/item failures;
- growing count of ended Lives with `room_cleanup_pending=1`;
- active Live with unavailable host;
- viewer counter drift/negative-value invariant violation;
- Redis hot-counter/materialization failures;
- room chat fan-out rate-limit pressure;
- queue depth and scheduler silence.

## Staging happy path

1. Host starts a Live with/without cover.
2. A second concurrent start returns the same active Live.
3. Guest and authenticated viewer join and track presence.
4. Viewer token cannot publish; host token can.
5. Comment is visible to another connected participant and recoverable from history.
6. Reactions broadcast.
7. Invite/request co-host; accept; obtain publishing token; activate.
8. Remove co-host; verify disconnection and no new co-host token.
9. End Live twice; both calls are safe.
10. Verify room deletion, zero current viewers, final metrics, profile/feed removal.

## Failure/recovery checklist

- Stop LiveKit, start/end Live, restore LiveKit, run reconciliation.
- Drop webhook delivery temporarily, restore it, verify duplicate/out-of-order events do not drift counters or revive ended Live.
- Kill a viewer connection without `track_leave`; verify two-minute grace and reconciliation close.
- Disable/block participant while connected; verify new authorization denied and removal queued.
- Disable host; run reconciliation and verify terminal state/room cleanup.
- Fail notification delivery; verify Live start still commits.
