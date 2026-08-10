# AOS Calls backend

AOS Calls is the existing one-to-one Connect calling domain. The authoritative model supports **audio** and **video** calls between the two users of an `AOS Conversation`. Group calls, a persistent RTC participant/session ledger, call recording/replay, backend mute/camera state, and Calls-owned LiveKit webhooks are not modeled and are not invented by this hardening.

## Authoritative lifecycle

`initiated -> ringing -> ongoing -> ended`

Existing terminal alternatives are `cancelled`, `rejected`, `missed`, and `failed`. Terminal states never transition back to an active state. `failed` is also the existing recovery terminal used when an ongoing call loses account/social eligibility or a LiveKit room is confirmed absent by reconciliation.

The caller is always `frappe.session.user`. The peer is derived from the canonical conversation; clients cannot provide caller/receiver identity, room name, RTC identity, or grants. A participant may have only one active (`initiated`, `ringing`, `ongoing`) one-to-one call at a time. Endpoint mutations serialize participant rows in deterministic order, and `AOS Call` keeps a document-level invariant for Desk/internal writes.

## Privacy and policy

Calls reuses Accounts account-state checks and the canonical Social blocking repository. Blocking in either direction prevents new calls, ringing/accept interaction, token issuance/reconnect, status access to an active call, and video upgrade interaction. Reject/cancel/end remain available as safe termination actions. Public call payloads expose opaque `ACC-*` identities, not User/email identities.

## Incoming and missed calls

`aos_incoming_call` is a transient incoming-call delivery, not a persistent notification-center record. Delivery uses the hardened notification delivery/outbox path, high priority, a 30-second TTL, and active device-token fanout. Android receives this event as data-only FCM with a per-call collapse key so the app background handler can present native incoming-call UI; normal notifications are unchanged. iOS/web retain the repository's existing alert+data delivery because APNs PushKit/VoIP-token infrastructure is not modeled here. The mobile application is responsible for consuming the transient call payload and presenting CallKit/ConnectionService UI.

`missed_call` remains the persistent notification semantic. Timeout finalization is atomic and idempotent; an accepted/rejected/cancelled/ended call cannot later become missed. The one-minute scheduler is the recovery fallback for a missed timeout worker.

## Recovery

Clients should call `get_call_status` after background/terminated restoration or after missing realtime events. The server state is authoritative. A receiver cannot mint an RTC token until the call is `ongoing`. An authorized ongoing reconnect may mint a fresh short-lived token and clears a pending missing-room observation. Terminal calls cannot reconnect.

See `api.md`, `realtime.md`, `livekit.md`, `operations.md`, and `testing.md` for contracts and runbooks.
