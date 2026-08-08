# Chat realtime

Persistent Chat state is authoritative in MariaDB. Realtime is an optimization for low-latency delivery; clients recover by reloading conversation/message state after reconnect.

## Persistent events

These are registered after commit:

| Event | Target | Key payload |
|---|---|---|
| `aos_new_message` | receiver | `conversation_id`, serialized `message` |
| `aos_message_edited` | receiver | `conversation_id`, serialized `message` |
| `aos_messages_deleted` | receiver | `conversation_id`, deleted message IDs/scope/display state |
| `aos_message_reaction_updated` | receiver | `conversation_id`, `message_id`, aggregate reactions, receiver viewer state |
| `aos_message_status` | sender | `conversation_id`, `delivered|read`, bounded `message_ids`, total count/status timestamp |

A failed websocket publication never rolls back the message or status mutation.

## Transient events

- `aos_typing`: direct, transient, debounced/rate-limited by clients and backend; no persistent typing row.
- `aos_presence_update`: derived from persisted `last_active`; broadcast only to relevant, non-blocked conversation peers.

Typing/presence can be lost without data corruption. Clients should expire stale typing UI locally and use history/status reloads after reconnect.

## Ordering

Message history is authoritative and deterministically ordered by `(creation, name)`. Clients should de-duplicate by message ID because websocket retries or reconnect recovery may surface the same committed state more than once.
