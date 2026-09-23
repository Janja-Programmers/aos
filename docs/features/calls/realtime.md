# Calls realtime and incoming delivery

Realtime accelerates synchronization; the durable `AOS Call` row is authoritative. Every lifecycle publish uses `after_commit=True`, and client payloads include the opaque public call ID plus monotonic `state_version` so clients can ignore duplicate or older events.

Existing events are preserved, with one readiness event added for fail-closed provisioning:

- `aos_call_ready` — caller only, after room provisioning + durable incoming dispatch
- `aos_incoming_call` — receiver only
- `aos_call_ringing` — caller only
- `aos_call_accepted` — both participants, so every authenticated session converges
- `aos_call_rejected` — both participants
- `aos_call_cancelled` — both participants when an incoming call had actually been dispatched
- `aos_call_ended` — both participants
- `aos_call_not_answered` — both participants with receiver-side missed semantics
- video-upgrade requested/accepted/declined/cancelled events

Payloads use the shared public serializer: public call/conversation IDs, opaque Accounts identities, safe display metadata, call type/durable `status`, transport `event_status`, timestamps/duration, `rtc_ready`, `ring_expires_at`, and `state_version`. They do not expose Frappe Call names, User/email IDs, room names, LiveKit JWTs, provider secrets, or push tokens.

## Multi-device and stale events

Durable lifecycle is account-scoped rather than device-scoped. Duplicate accepts/ends from multiple authenticated sessions converge through locked/idempotent mutations. Tokens use the same server-controlled public account identity; clients cannot choose a second RTC identity to bypass call membership.

A reconnecting or background-restored client should call `get_call_status`. `can_show_incoming_ui`, `can_accept`, and `can_join` are derived from current durable status/policy/readiness, not from a cached realtime event. A stale native action therefore cannot revive a cancelled, missed, ended, blocked, or failed call.

## Notifications

`aos_incoming_call` remains transient delivery through the hardened Notifications outbox/fanout contract. `missed_call` remains the persistent notification-center semantic. Notification deduplication keys use the opaque public call ID; Calls does not implement a second push pipeline.

The existing platform-specific notification-delivery behavior is unchanged by this backend pass.
