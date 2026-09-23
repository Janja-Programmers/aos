# Calls API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `accept_call` | POST | Session required | Client |
| `cancel_call` | POST | Session required | Client |
| `clear_call_history` | POST | Session required | Client |
| `delete_call_logs` | POST | Session required | Client |
| `end_call` | POST | Session required | Client |
| `get_call_group_details` | GET/POST | Session required | Client |
| `get_call_status` | GET/POST | Session required | Client |
| `get_call_token` | POST | Session required | Client |
| `initiate_call` | POST | Session required | Client |
| `list_calls` | GET/POST | Session required | Client |
| `mark_call_ringing` | POST | Session required | Client |
| `reject_call` | POST | Session required | Client |
| `request_video_upgrade` | POST | Session required | Client |
| `respond_video_upgrade` | POST | Session required | Client |
<!-- END CODE-DERIVED ENDPOINTS -->

All Calls methods require authentication. Public wrappers accept only documented business fields after stripping Frappe's `cmd` transport field; unknown fields fail closed. Call IDs are canonical opaque `call_<32 hex>` values. The internal Frappe Call name and LiveKit room name are not client contracts.

| Endpoint | Intended method | Allowed fields | App limit/min/user |
|---|---|---|---:|
| `initiate_call` | POST | `conversation_id`, `call_type` | 10 |
| `mark_call_ringing` | POST | `call_id` | 120 |
| `accept_call` | POST | `call_id` | 60 |
| `reject_call` | POST | `call_id` | 60 |
| `cancel_call` | POST | `call_id` | 60 |
| `end_call` | POST | `call_id` | 60 |
| `request_video_upgrade` | POST | `call_id` | 30 |
| `respond_video_upgrade` | POST | `call_id`, `action` | 60 |
| `get_call_status` | GET/POST | `call_id` | 120 |
| `get_call_token` | POST | `call_id` | 120 |
| `list_calls` | GET/POST | `limit`, `conversation_id`, `type`, `cursor_created_at`, `cursor_call_id` | 120 |
| `get_call_group_details` | GET/POST | `latest_call_id`, `oldest_call_id` | 120 |
| `delete_call_logs` | POST | `call_ids` (max 100) | 60 |
| `clear_call_history` | POST | none | 20 |

Initiation also applies the existing aggregate incoming-attempt protection per target account. Shared bounded/hashed rate-limit keys are reused.

## Initiation and RTC readiness

`initiate_call` persists or reuses the one active caller/receiver call under deterministic locks, then queues shared-LiveKit room provisioning after commit. A newly created/pending call returns its public call payload with `rtc_ready=false` and no token/room name. Once provisioning has been durably dispatched, the caller receives `aos_call_ready`; `get_call_token` then returns a fresh short-lived caller token while the durable `ring_expires_at` window remains open. Retrying `initiate_call` for that same ready active call is idempotent and may return a fresh caller token.

## Lifecycle authorization

- Ring: receiver only; requires the call to be durably RTC-ready and not past `ring_expires_at`. Repeated `ringing` is idempotent.
- Accept: receiver only; policy/readiness and the ring deadline are rechecked. Repeated `ongoing` accept is idempotent and returns a fresh receiver token.
- Reject: receiver only while ringing-capable; repeated `rejected` is idempotent.
- Cancel: caller only while ringing-capable; repeated `cancelled` is idempotent.
- End: either participant while `ongoing`; repeated `ended` is idempotent.
- Token: participant only; receiver cannot mint before `ongoing`; terminal calls cannot mint.
- Video upgrade: active participants only; request/response is serialized and identical retries are idempotent. After acceptance, clients refresh via `get_call_token` to obtain the video-scoped camera grant.

Account enabled/deleted state and bidirectional Social block policy are rechecked at interaction/token boundaries. The server owns all timestamps, duration, room identity, participant identity, grants, and state transitions.

## History

History is participant-only and uses per-user visibility flags rather than deleting the shared audit row. Lists use keyset/cursor pagination with a maximum page size of 100 and batch Accounts projection. The client sends the opaque boundary `cursor_call_id` plus its returned timestamp; the server resolves the authorized call and uses the database-owned creation/name boundary for SQL ordering rather than trusting a client sort key. Bulk log deletion is bounded to 100 call IDs, and clear-history work is batched.
