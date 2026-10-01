# AOS Calls backend

AOS Calls supports direct and conference audio/video calling while consuming the hardened Authentication, Accounts, Social, Notifications, and shared LiveKit contracts. Direct calls contain two participants; group calls contain 3–32 total participants. Calls does not own a second RTC client/admin stack, token signer, webhook verifier, Redis correctness layer, or notification pipeline.

## Durable model

`AOS Call` owns the conference-wide state and an opaque `call_<32 hex>` public ID. `AOS Call Participant` owns per-account invitation/ringing/join/decline/missed/leave state. The internal Frappe Call name, User names/emails, and LiveKit room name are never client identifiers.

Call lifecycle: `initiated -> ringing -> ongoing -> ended`, with terminal `cancelled`, `rejected`, `missed`, and `failed`. Participant lifecycle: `invited -> ringing -> joined -> left`, with `declined`, `missed`, `failed`, and `cancelled` alternatives. `state_version` is monotonic so clients can discard stale realtime updates.

For group calls, one decline/miss does not terminate the conference. The initiator is an audit/invitation role, not the room lifetime owner: leaving an ongoing group call does not end it while other joined participants remain. Direct calls preserve two-party end semantics.

## Public contract

`initiate_call` accepts server-resolved opaque `participant_ids`: one target creates a direct call and 2–31 targets create a group call. The hard server maximum is 32 people including the initiator. A joined participant may invite additional accounts while capacity remains; adding to an ongoing direct call atomically promotes it to group mode without recreating the RTC room. Calls started outside Chat may omit `conversation_id`. A Chat-bound direct call must match the exact active two-member direct conversation; a Chat-bound group call must match the exact active membership of a group conversation and therefore is available only while that group contains 3–32 active members.

Room capacity, room names, RTC identities, token grants, timestamps, participant state, and ownership are server-controlled. Realtime accelerates UX but durable database state remains authoritative.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `accept_call` | POST | Session required | Client |
| `add_call_participants` | POST | Session required | Client |
| `cancel_call` | POST | Session required | Client |
| `clear_call_history` | POST | Session required | Client |
| `delete_call_logs` | POST | Session required | Client |
| `end_call` | POST | Session required | Client |
| `get_call_status` | GET/POST | Session required | Client |
| `get_call_token` | POST | Session required | Client |
| `initiate_call` | POST | Session required | Client |
| `list_calls` | GET/POST | Session required | Client |
| `mark_call_ringing` | POST | Session required | Client |
| `reject_call` | POST | Session required | Client |
| `request_video_upgrade` | POST | Session required | Client |
| `respond_video_upgrade` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

All methods require authentication and strict allowlisted request fields. Client call IDs are opaque `call_<32 hex>` values.

| Endpoint | Fields | Limit/min/user |
|---|---|---:|
| `initiate_call` | `participant_ids`, `conversation_id`, `call_type` | 10 |
| `mark_call_ringing` | `call_id` | 120 |
| `accept_call` | `call_id` | 60 |
| `reject_call` | `call_id` | 60 |
| `cancel_call` | `call_id` | 60 |
| `end_call` | `call_id` | 60 |
| `add_call_participants` | `call_id`, `participant_ids` | 30 |
| `request_video_upgrade` | `call_id` | 30 |
| `respond_video_upgrade` | `call_id`, `action` | 60 |
| `get_call_status` | `call_id` | 120 |
| `get_call_token` | `call_id` | 120 |
| `list_calls` | `limit`, `conversation_id`, `type`, `cursor_created_at`, `cursor_call_id` | 120 |
| `delete_call_logs` | `call_ids` (max 100) | 60 |
| `clear_call_history` | none | 20 |

`participant_ids` contain opaque Accounts IDs, never User/email identities. One target means direct; 2–31 targets means group. Total membership may never exceed 32. `conversation_id` is optional for calls started outside Chat. When supplied, a direct call must exactly match a two-member direct Chat conversation, while a group call must exactly match all active members of a group Chat conversation (3–32 total).

Accept/reject/ring state is participant-scoped. `cancel_call` is initiator-only before anyone accepts. During an ongoing direct call, `end_call` ends the call for both sides. During an ongoing group call, `end_call` means the current participant leaves; the call becomes terminal only when no joined participants remain. `add_call_participants` requires a joined participant in an ongoing call. On a direct call, the first successful addition atomically promotes it to group mode and clears the one-to-one conversation/video-upgrade state without recreating the LiveKit room.

Tokens are issued only to joined participants. Direct audio calls retain the existing server-authorized audio-to-video upgrade flow; group calls choose audio/video at initiation and do not use the direct-call upgrade handshake.

History is participant-scoped, cursor-paginated, bounded, and uses batched Accounts projections. Hidden history is per participant and never deletes the shared audit record.

## Realtime and delivery

Database state is authoritative. All Calls realtime publishes use `after_commit=True` and carry opaque Calls/Accounts identities plus monotonic `state_version`.

Core events include `aos_call_ready`, `aos_incoming_call`, participant ringing/joined/declined/left/missed events, `aos_call_cancelled`, `aos_call_ended`, and direct-call video-upgrade events. Payloads include the participant roster projection and current participant state, but never internal User names, Frappe Call names, room names, JWTs, or provider secrets.

Incoming call push is transient through hardened Notifications; missed-call notification is persistent. Every invited participant has an independent durable `ring_expires_at`. Delivery suppression revalidates the recipient participant row and the actual inviter (including a non-initiator who adds somebody to an ongoing group) before delivery, preventing stale or newly-blocked invites.

Multi-device retries converge through row-locked/idempotent mutations. Reconnects reconcile through `get_call_status` and `get_call_token`; for a pending direct call, status reconciliation may idempotently terminalize an invite whose durable `ring_expires_at` has already elapsed instead of waiting for the next minute-scheduler tick. Stale realtime/native actions cannot revive terminal or expired participant state.

## Shared RTC dependencies

Calls consumes the production-ready shared LiveKit services. The application membership cap is 2 for a direct call and 32 for a group call. The shared provider room is server-provisioned with a 32-seat ceiling from the start so an ongoing direct call can be promoted to a conference without deleting/recreating RTC state. That provider ceiling is not authorization: only durable joined members can receive server-generated tokens.

Provider room creation occurs outside database row locks. After the provider returns, Calls re-locks/revalidates durable state and Social/Accounts policy before setting `rtc_provisioned_at` and dispatching per-recipient incoming invitations. Failure is closed: no incoming signal or join token is exposed.

Call tokens remain short-lived and least privilege. Audio may publish microphone only; video may publish microphone+camera. Participants may subscribe but cannot publish LiveKit data or mutate participant metadata. Room identity, participant identity, grants, and TTL are server-controlled. Live uses its separate existing token path and is not changed by conference Calls.

Group rooms are provisioned at the server maximum from the start so adding participants does not require recreating the room. Durable membership still controls who may receive a token; LiveKit room capacity alone never authorizes joining.

## Operations

The minute Calls scheduler performs bounded provisioning recovery and participant ring-expiry processing. There is no sleeping worker per invited participant. Pending direct-call status reconciliation also idempotently finalizes an already-expired invite, closing the scheduler-granularity gap while keeping the database authoritative. The minute worker remains the fleet-wide safety net. Missed state is per participant; an expired invite cannot terminate a conference that already has joined participants.

The five-minute reconciliation path retries durable room cleanup and reconciles active LiveKit rooms/account/block policy. Group initiator disappearance does not make the room terminal while other joined participants remain.

Schema-only indexes are installed by `install_call_indexes` and `install_call_public_indexes` after final DocType synchronization.

## Testing

Calls tests cover strict public inputs, opaque identities, normalized schema, server-owned 2/32 LiveKit capacity, least-privilege grants, participant-scoped lifecycle, notification suppression, cursor history, indexes, and shared Live regression safety.

Database contracts cover direct two-person behavior, 32-person conference capacity and overflow rejection, one-time provisioning fan-out, per-participant reject/missed semantics, multiple acceptors, initiator leave without conference termination, adding participants during an ongoing conference, token gating, direct end semantics, history privacy, and final-schema invariants.

For acceptance run the Calls unit/source/LiveKit/database modules, Notifications source guards, shared Live tests, then the complete AOS backend suite. Provider calls should remain mocked in DB contract tests; ordinary fixtures must stay transaction-local.

## Architecture
Calls owns durable Call and participant records, participant-scoped state changes, and the public Call identifier. LiveKit owns provider room/token primitives; Notifications owns durable delivery. Room provisioning runs in bounded workers outside row locks and revalidates committed Call state before recipient fanout. No one web node is the authority for call presence.
