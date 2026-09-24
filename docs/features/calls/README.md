# AOS Calls backend

AOS Calls supports direct and conference audio/video calling while consuming the hardened Authentication, Accounts, Social, Notifications, and shared LiveKit contracts. Direct calls contain two participants; group calls contain 3–32 total participants. Calls does not own a second RTC client/admin stack, token signer, webhook verifier, Redis correctness layer, or notification pipeline.

## Durable model

`AOS Call` owns the conference-wide state and an opaque `call_<32 hex>` public ID. `AOS Call Participant` owns per-account invitation/ringing/join/decline/missed/leave state. The internal Frappe Call name, User names/emails, and LiveKit room name are never client identifiers.

Call lifecycle: `initiated -> ringing -> ongoing -> ended`, with terminal `cancelled`, `rejected`, `missed`, and `failed`. Participant lifecycle: `invited -> ringing -> joined -> left`, with `declined`, `missed`, `failed`, and `cancelled` alternatives. `state_version` is monotonic so clients can discard stale realtime updates.

For group calls, one decline/miss does not terminate the conference. The initiator is an audit/invitation role, not the room lifetime owner: leaving an ongoing group call does not end it while other joined participants remain. Direct calls preserve two-party end semantics.

## Public contract

`initiate_call` accepts server-resolved opaque `participant_ids`: one target creates a direct call and 2–31 targets create a group call. The hard server maximum is 32 people including the initiator. A joined participant may invite additional accounts while capacity remains; adding to an ongoing direct call atomically promotes it to group mode without recreating the RTC room. Calls started outside Chat may omit `conversation_id`. A Chat-bound direct call must match the exact active two-member direct conversation; a Chat-bound group call must match the exact active membership of a group conversation and therefore is available only while that group contains 3–32 active members.

Room capacity, room names, RTC identities, token grants, timestamps, participant state, and ownership are server-controlled. Realtime accelerates UX but durable database state remains authoritative.
