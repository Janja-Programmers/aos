# Chat API

Base form: `/api/method/aos.api.v1.chat.<endpoint>`.

Frappe wraps the domain response in its normal `message` property. Domain success/failure envelopes remain:

```json
{"ok":true,"message":"...","data":{}}
```

```json
{"ok":false,"message":"...","error":"CHAT_*","data":{}}
```

Clients must branch on `error`, not the human message.

## Public endpoints

| Endpoint | HTTP | Accepted domain fields |
|---|---|---|
| `open_conversation` | POST | `user` |
| `list_conversations` | GET/POST | `limit`, `offset` |
| `delete_conversation` | POST | `conversation_id` |
| `send_message` | POST | `conversation_id`, `content`, `attachments`, `ad`, `short`, `live`, `reply_to_message`, `idempotency_key` |
| `list_messages` | GET/POST | `conversation_id`, `limit`, `before` |
| `forward_message` | POST | `message_id`, one of `target_conversation_id` / `target_conversation_ids`, optional `idempotency_key` |
| `edit_message` | POST | `message_id`, `content` |
| `delete_messages` | POST | one of `message_id` / `message_ids`, `delete_scope`=`me|everyone` |
| `clear_chat` | POST | `conversation_id` |
| `toggle_message_star` | POST | `message_id` |
| `list_starred_messages` | GET/POST | `limit`, optional `conversation_id`, optional legacy `before` |
| `toggle_message_reaction` | POST | `message_id`, `emoji` |
| `translate_message` | POST | `message_id`, `target_language`, optional `source_language`, `force_refresh` |
| `mark_delivered` | POST | `conversation_id` |
| `mark_read` | POST | `conversation_id` |
| `send_typing_event` | POST | `conversation_id`, `is_typing` |
| `get_presence` | GET/POST | `conversation_id` |

`cmd` is accepted only as Frappe transport metadata. Other unknown fields fail closed.

`list_conversations` includes `last_message_is_mine`, `last_message_id`, `last_message_delivered_at`, and `last_message_read_at` for WhatsApp-style receipt rendering. Receipt fields are populated only for the current viewer's latest visible outgoing preview. Clients render one sent tick when both receipt timestamps are null, two delivered ticks when only `last_message_delivered_at` is set, and two read ticks when `last_message_read_at` is set. Realtime clients must apply `aos_message_status` to a conversation preview only when the event `message_ids` contains that row's `last_message_id`; this prevents delayed status events for older messages from advancing a newer preview. Incoming previews keep their existing presentation and must not infer receipt state from these fields.


## Identifiers and bounds

- Conversation: `CONV-YYYY-NNNNN`
- Message: `MSG-YYYY-NNNNN`
- Live: `LIVE-YYYY-NNNNN`
- Short: `SHORT-YYYY-NNNNN`
- Ad: `AD-YYYY-NNNNN`
- Media: `MEDIA-YYYY-NNNNN`
- Public account: canonical `ACC-*` where an account is serialized.
- Message text: 4000 Unicode code points after NFC normalization and trimming.
- Attachments: maximum 10 per request/message.
- Multi-delete: maximum 100 message IDs.
- Forward targets: maximum 20 conversations.
- `limit`: 1–100. Conversation offset: 0–10000.
- Client idempotency key: maximum 128 characters; the database stores only a server-derived digest.
- JSON body: maximum 64 KiB and nesting depth 8 at the Chat boundary.

## Stable Chat errors

| Error | HTTP | Meaning |
|---|---:|---|
| `CHAT_INVALID_REQUEST` | 422 | Invalid request shape/value |
| `CHAT_UNKNOWN_FIELD` | 422 | Unsupported request field |
| `CHAT_ALIAS_CONFLICT` | 422 | Conflicting compatible aliases |
| `CHAT_INVALID_IDENTIFIER` | 422 | Malformed public ID |
| `CHAT_INPUT_TOO_LARGE` | 413 | Body/list/text bound exceeded |
| `CHAT_ACCESS_DENIED` | 403 | Membership, block or object-access denial |
| `CHAT_NOT_FOUND` | 404 | Missing/non-enumerable resource |
| `CHAT_CONFLICT` | 409 | Concurrent or uniqueness conflict |
| `CHAT_INVALID_STATE` | 409 | Mutation not valid for current state |
| `CHAT_RATE_LIMITED` | 429 | Domain service rate rejection |
| `CHAT_DEPENDENCY_UNAVAILABLE` | 503 | Required dependency unavailable |
| `CHAT_INTERNAL_ERROR` | 500 | Sanitized unexpected failure |

Shared authentication/account/media/translation codes remain backward-compatible where already public.

## Native object examples

```json
{
  "conversation_id":"CONV-2026-00001",
  "live":"LIVE-2026-00015",
  "content":"Come watch"
}
```

Generic `send_message` accepts a Live reference only while that Live is active. New Live sharing should normally use the feature-owned endpoint documented in `sharing.md`.


### Starred-message navigation

`list_starred_messages` includes the canonical public `conversation_id` on each returned message so web/mobile clients can reopen the exact conversation containing the starred message. This does not expose participant internals; callers only receive starred messages from conversations they already belong to.
