# Calls API

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

`participant_ids` contain opaque Accounts IDs, never User/email identities. One target means direct; 2–31 targets means group. Total membership may never exceed 32. Group calls cannot bind to the one-to-one Conversation model.

Accept/reject/ring state is participant-scoped. `cancel_call` is initiator-only before anyone accepts. During an ongoing direct call, `end_call` ends the call for both sides. During an ongoing group call, `end_call` means the current participant leaves; the call becomes terminal only when no joined participants remain. `add_call_participants` requires a joined participant in an ongoing call. On a direct call, the first successful addition atomically promotes it to group mode and clears the one-to-one conversation/video-upgrade state without recreating the LiveKit room.

Tokens are issued only to joined participants. Direct audio calls retain the existing server-authorized audio-to-video upgrade flow; group calls choose audio/video at initiation and do not use the direct-call upgrade handshake.

History is participant-scoped, cursor-paginated, bounded, and uses batched Accounts projections. Hidden history is per participant and never deletes the shared audit record.
