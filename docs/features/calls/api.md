# Calls API

All Calls v1 methods require authentication. The public wrapper accepts only the documented business fields after stripping Frappe's `cmd` transport field. Unknown fields fail closed. `CALL-*` and `CONV-*` inputs must use canonical public identifiers. Public errors are normalized to stable `CALL_*` categories while existing successful response shapes are preserved.

| Endpoint | Method | Allowed fields | App limit/min/user |
|---|---|---|---:|
| `initiate_call` | POST | `conversation_id`, `call_type` | 10 |
| `mark_call_ringing` | POST | `call_id` | 120 |
| `accept_call` | POST | `call_id` | 60 |
| `reject_call` | POST | `call_id` | 60 |
| `cancel_call` | POST | `call_id` | 60 |
| `end_call` | POST | `call_id` | 60 |
| `request_video_upgrade` | POST | `call_id` | 30 |
| `respond_video_upgrade` | POST | `call_id`, `action` | 60 |
| `get_call_status` | GET/POST | `call_id` or legacy `id` alias | 120 |
| `get_call_token` | POST | `call_id` | 120 |
| `list_calls` | GET/POST | `limit`, `conversation_id`, `type`, cursor pair | 120 |
| `get_call_group_details` | GET/POST | `latest_call_id`, `oldest_call_id` | 120 |
| `delete_call_logs` | POST | `call_ids` (max 100) | 60 |
| `clear_call_history` | POST | none | 20 |

Initiation also applies an aggregate 20 incoming-attempts/minute limit per target account. All application keys use the shared bounded/hashed rate-key helper. The public endpoint registry retains its authenticated Nginx baseline entries for all 14 methods.

## Lifecycle authorization

- Initiate: session caller only; peer derived from conversation; account/block checks; deterministic participant lock; global per-participant active-call check.
- Ring: intended receiver only while `initiated`/`ringing`; retry of `ringing` is idempotent.
- Accept: intended receiver only while `initiated`/`ringing`; repeated `ongoing` accept is idempotent and returns a fresh receiver RTC token.
- Reject: intended receiver only while ringing-capable; repeated `rejected` is idempotent.
- Cancel: caller only while ringing-capable; repeated `cancelled` is idempotent.
- End: either participant while `ongoing`; repeated `ended` is idempotent.
- Video upgrade: active participant only; request/response is serialized and repeated identical actions are idempotent.

The server owns timestamps and duration. State transition inputs are never accepted from clients.

## History

History is participant-only and uses per-user visibility flags rather than deleting the shared audit row. Lists are cursor-paginated with a maximum page size of 100 and batch public-profile serialization. Group details are bounded to 5,000 raw rows. `clear_call_history` mutates in 500-row batches.
