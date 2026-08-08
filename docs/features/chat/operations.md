# Chat operations

## Deployment checklist

```bash
bench --site <site> backup --with-files
bench --site <site> set-maintenance-mode on
# deploy cumulative AOS code
bench --site <site> migrate
bench --site <site> clear-cache
bench restart
```

Validate Chat before taking the site out of maintenance mode.

## Rate limits

Per authenticated user, per minute unless noted:

- open conversation 60
- list conversations 120
- delete conversation 60
- send message 120
- forward 60
- edit 30
- delete messages 60
- clear chat 20
- star 120
- list stars 120
- reaction 120
- translate 60
- list messages 300
- delivered/read 600 each
- typing 600
- Live `share_live_to_chat` 30

Presence broadcast is separately throttled to 10 seconds.

## Observability

The Chat API boundary writes privacy-safe structured categories: operation, outcome, bounded reason, latency/count where supplied. Do not add message text, search text, raw cursors, IP addresses, account IDs, conversation IDs or message IDs to Chat logs.

Dependency failures should be logged by bounded category; user-facing errors remain sanitized. Chat API code must not capture/log raw tracebacks or request payloads because those may contain private message text or identifiers.

## Failure recovery

- Realtime unavailable: committed data remains authoritative; reconnect/list messages.
- Notification delivery unavailable: message persists; outbox/retry infrastructure handles delivery when available.
- Duplicate send retry: same idempotency operation resolves to the committed message.
- Migration interrupted: rerun `bench migrate`; patches are designed to be idempotent/bounded.
- Translation provider failure: return sanitized dependency error; do not mutate message text.
- Shared Live/Short later inaccessible: history returns an unavailable preview, not stale private metadata.
