# AOS Calls backend

AOS Calls is the existing one-to-one Connect calling domain. The authoritative model supports **audio** and **video** calls between the two users of an `AOS Conversation`. This hardening keeps Calls small: authentication, Accounts projection/state, Social blocking, Notifications delivery, and LiveKit RTC infrastructure remain owned by their hardened shared domains.

Calls does **not** own a second LiveKit client/admin stack, token signer, webhook verifier, Redis correctness layer, or RTC configuration.

## Authoritative lifecycle

The persisted lifecycle remains compatible with the existing feature:

`initiated -> ringing -> ongoing -> ended`

Terminal alternatives are `cancelled`, `rejected`, `missed`, and `failed`. `initiated` includes the short provider-provisioning phase; `rtc_ready=false` means a client must not attempt to join yet. A monotonically increasing `state_version` accompanies client-visible state so clients can discard duplicate/stale realtime events.

The caller is always `frappe.session.user`. The peer is derived from the canonical conversation. Clients cannot supply caller/receiver identity, room name, RTC identity, grants, or lifecycle state. Participant rows and call rows are serialized for critical mutations, while LiveKit network calls happen outside database row locks/transactions.

## Public identity boundary

`AOS Call.name` remains an internal Frappe key used for links and SQL ordering. Client-facing Calls APIs, history cursors, realtime events, push payloads, and LiveKit call metadata use a random opaque `call_<32 hex>` `public_id`. Internal names and LiveKit room names are not returned to clients.

LiveKit room names are independently randomized server-side and remain immutable. RTC participant identity is the shared Accounts public account ID, never a User/email value.

## Incoming, missed, and recovery

Creation first persists an `initiated` call and queues idempotent room provisioning. The shared LiveKit admin service provisions a room limited to two participants. Only after provisioning succeeds and policy/state are revalidated does the backend durably mark the call RTC-ready, publish `aos_call_ready` to the caller, dispatch `aos_incoming_call` to the receiver, and start the unanswered timeout clock.

Provisioning failure fails closed to `failed`; no join token is exposed. There is no sleeping worker per call. The minute scheduler performs bounded indexed recovery of lost provisioning enqueues and bounded missed-call finalization based on the durable `ring_expires_at` deadline.

`missed_call` remains the persistent notification semantic. Incoming delivery remains transient and uses the hardened Notifications delivery/outbox path.

## Recovery authority

Realtime accelerates UX but is never authoritative. Clients reconcile with `get_call_status` after reconnect, app restoration, or stale native actions. Terminal calls cannot reconnect. An authorized `ongoing` participant may mint a fresh short-lived token after transient RTC disconnect.

See `api.md`, `realtime.md`, `livekit.md`, `operations.md`, and `testing.md`.
