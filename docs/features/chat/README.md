# AOS Chat

## Overview

AOS Chat is the canonical authenticated one-to-one messaging domain. It owns conversation membership, durable message records, replies, forwarding, edit/delete semantics, private stars, reactions, translation cache, delivery/read state, conversation visibility, unread counters, typing/presence acceleration, Chat notification requests, and Chat realtime events.

Chat is database-authoritative. Redis and realtime delivery are acceleration only. A missed, duplicated, delayed, or out-of-order socket event never changes durable truth; clients reconcile by reading the canonical list endpoints.

The final backend supports **one-to-one conversations only**. Group Chat is not part of the current data model and no group API is exposed. Calls may independently support group calling; Chat does not duplicate that model.

## Responsibilities

Chat owns:

- one conversation per unordered account pair;
- membership and per-participant active/hidden state;
- message ordering and message lifecycle;
- per-participant unread, delivery and read state;
- reply references;
- forwarding provenance;
- private message stars;
- one reaction per user/message;
- translation cache rows;
- Chat-to-Media linkage rows;
- minimal canonical references to Ads, Shorts, Live sessions, and Calls;
- notification requests for new messages;
- authenticated user-targeted realtime Chat events.

Chat does not own authentication, account/profile truth, relationship blocking, media storage, Ads/Shorts/Live projections, push infrastructure, Calls state, or LiveKit RTC state.

## Boundaries

- **Authentication** supplies the authenticated Frappe session. Public Chat endpoints never accept a sender/user override for mutations.
- **Accounts** owns public `ACC-*` identities and display/profile projection.
- **Social** owns blocking/privacy relationship rules. Chat consumes the shared blocking helpers.
- **Media** owns upload lifecycle, MIME/type, original filename, dimensions, duration, private-object authorization, signed URLs, and storage. Chat stores only Media linkage.
- **Notifications** owns durable/in-app/push notification delivery and retry behavior.
- **Ads** owns `ad_*` public IDs and shareable Ad projection.
- **Shorts** owns `SHR-*` IDs and visibility/projection.
- **Live** owns `LIVE-*` IDs and live-session state/projection.
- **Calls** owns `call_*` IDs and the call state machine. Chat may contain a system message carrying the public call ID; it does not control Calls or LiveKit.
- **LiveKit** is never invoked by Chat directly.

## Architecture

Public traffic enters `aos.api.v1.chat.*`. Those wrappers are intentionally thin and call `aos.services.chat.api.run_chat_api`, which performs strict request validation, stable public-error normalization, per-operation savepoint handling, callback restoration, and structured observability.

Implementation modules under `aos.api.chat` perform authorized use cases. Shared Chat domain helpers under `aos.services.chat` own identifiers, cursors, locking, realtime-after-commit, cross-feature projections, and feature-owned Chat adapters. Business rules are not duplicated in the v1 wrappers.

Write paths use the database for serialization/uniqueness. No Chat correctness rule depends on a Python global, process-local lock, sticky session, or worker-local cache.

## Data Model

### AOS Conversation

A one-to-one conversation. `name` is the public conversation ID. Important fields are `participant_1`, `participant_2`, `pair_key`, per-participant `is_active_*`, `unread_count_*`, and per-participant last-visible-message preview fields.

`pair_key` is a SHA-256 digest of the sorted internal participant references and is database-unique. Participant order is deterministic. The conversation public ID is opaque and not derived from participants.

### AOS Message

A durable message. `name` is the public message ID. Important fields include `conversation`, `sender`, `message_type`, `content`, canonical `ad` / `short` / `live` references, public `call_id` for Calls-owned system messages, `reply_to_message`, edit/delete state, delivery/read timestamps, forwarding provenance, `idempotency_key`, and `idempotency_request_hash`.

The stored idempotency key is a server-side hash, never the raw client key.

### AOS Message Attachment

A linkage record only: `message`, `media`, `sort_order`. It does not duplicate filename, MIME type, dimensions, duration, URL, storage key, or visibility. Those remain Media-owned.

### AOS Message Star

Private user state linking `message`, `conversation`, and `user`. `(message, user)` is unique.

### AOS Message Reaction

User reaction state linking `message`, `conversation`, `user`, and `emoji`. `(message, user)` is unique, so each user has at most one reaction on a message.

### AOS Message Translation

Cached translation result. The canonical cache identity is `(message, target_language, original_content_hash)`.

## Fields

Canonical Chat IDs:

- conversation: `CONV-` + 32 lowercase hexadecimal characters;
- message: `MSG-` + 32 lowercase hexadecimal characters;
- attachment linkage: `CMA-` + 32 lowercase hexadecimal characters.

High-write Chat records use random opaque IDs generated independently on every application node; no naming series or shared sequence is used.

Message types are `text`, `media`, `ad`, `short`, `live`, `mixed`, and backend-managed `system`. A message may contain text plus one or more owned-feature references, in which case `mixed` is used.

Shared references are minimal and canonical:

- Ad: Ads `public_id` (`ad_*`), never `AOS Ad.name`;
- Short: canonical Shorts ID (`SHR-*`);
- Live: canonical Live ID (`LIVE-*`);
- Call system projection: canonical Calls public ID (`call_*`);
- attachment: Media ID (`MEDIA-*`).

Unavailable/deleted shared entities remain referenced in history but serialize as unavailable previews rather than being copied into Chat permanent state.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `clear_chat` | POST | Session required | Client |
| `delete_conversation` | POST | Session required | Client |
| `delete_messages` | POST | Session required | Client |
| `edit_message` | POST | Session required | Client |
| `forward_message` | POST | Session required | Client |
| `get_presence` | GET | Session required | Client |
| `list_conversations` | GET | Session required | Client |
| `list_messages` | GET | Session required | Client |
| `list_starred_messages` | GET | Session required | Client |
| `mark_delivered` | POST | Session required | Client |
| `mark_read` | POST | Session required | Client |
| `open_conversation` | POST | Session required | Client |
| `send_message` | POST | Session required | Client |
| `send_typing_event` | POST | Session required | Client |
| `set_message_reaction` | POST | Session required | Client |
| `set_message_star` | POST | Session required | Client |
| `translate_message` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->


All routes are under `/api/method/aos.api.v1.chat.<method>`. All require an authenticated session. Request bodies/arguments are strict: unknown fields fail with `CHAT_UNKNOWN_FIELD`; removed aliases are not accepted.

| Method | HTTP | Accepted fields |
|---|---|---|
| `open_conversation` | POST | `user` |
| `list_conversations` | GET | `limit`, `cursor` |
| `delete_conversation` | POST | `conversation_id` |
| `send_message` | POST | `conversation_id`, `content`, `attachments`, `ad`, `short`, `live`, `reply_to_message`, `idempotency_key` |
| `list_messages` | GET | `conversation_id`, `limit`, `cursor` |
| `forward_message` | POST | `message_id`, `target_conversation_ids`, `idempotency_key` |
| `edit_message` | POST | `message_id`, `content` |
| `delete_messages` | POST | `message_ids`, `delete_scope` |
| `clear_chat` | POST | `conversation_id` |
| `set_message_star` | POST | `message_id`, `starred` |
| `list_starred_messages` | GET | `limit`, `conversation_id`, `cursor` |
| `set_message_reaction` | POST | `message_id`, `emoji` |
| `translate_message` | POST | `message_id`, `target_language`, `source_language`, `force_refresh` |
| `mark_delivered` | POST | `conversation_id` |
| `mark_read` | POST | `conversation_id` |
| `send_typing_event` | POST | `conversation_id`, `is_typing` |
| `get_presence` | GET | `conversation_id` |

`attachments` is a list of at most 10 objects and the only accepted attachment shape is `{ "media_id": "MEDIA-..." }`. Chat validates readiness/ownership through Media before a new send.

`target_conversation_ids` is a list of at most 20 canonical conversation IDs. `message_ids` is a list of at most 100 canonical message IDs. Singular aliases are not part of v1.

List responses use `data: { "items": [...], "next_cursor": <string|null> }`. Cursors are versioned opaque base64url positions. Conversation order is deterministic by viewer activity time then conversation ID. Message history is deterministic by creation time then message ID. Starred history is deterministic by star creation time then message ID. Deep offset pagination is not supported.

## Cross-feature Dependencies

### Media

New message attachments must be canonical private `chat_attachment` Media objects in a ready state. Chat inserts only the linkage row and asks Media to attach the object. Read serialization performs one bounded membership-aware Media URL projection for the page instead of one authorization query per attachment. Forwarded messages may reference the same immutable private Media object through an additional authorized Chat linkage.

### Accounts and Social

Internal Frappe User references remain persistence-level foreign keys where required by Frappe, but public identity projection uses Accounts. Blocking is evaluated through hardened Social helpers. A historical conversation ID does not grant interaction after a block.

### Notifications

New-message notification dispatch uses `NotificationService`. Durable notification/outbox behavior and retry semantics remain Notifications-owned. Chat does not implement a parallel push queue.

### Ads, Shorts, and Live

Chat stores only the canonical owning-feature ID and resolves a bounded preview at read time. Visibility is re-evaluated for the viewer. A missing/inactive/inaccessible entity produces an unavailable preview without leaking privileged fields.

### Calls

Calls owns the call state machine, participants, tokens, room lifecycle, and LiveKit interaction. A direct Call may carry a Chat conversation ID where the Calls contract allows it. Calls writes/updates one Chat system message per public `call_id`; database uniqueness prevents duplicate call-history messages under concurrent updates.

## Transaction / Concurrency Model

The caller/Frappe request transaction is authoritative. Chat does not manually commit. The v1 boundary creates an operation savepoint for mutations and restores Frappe transaction callbacks when rolling a handled failure back to that savepoint.

The lock order for Chat mutations is **conversation row(s) first, then message row(s)**, in deterministic ID order where multiple rows are involved. Database unique constraints arbitrate conversation creation, message idempotency, call system-message projection, stars, reactions, attachment duplication, and translation cache races.

### Message idempotency

`send_message` accepts an optional client `idempotency_key` of at most 128 characters. Chat stores a server digest scoped to the sender/conversation/operation and a hash of the canonical message request. Retrying the same key with the same canonical payload returns the existing message. Reusing that key for a different canonical payload returns `CHAT_CONFLICT` (`409`). Concurrent duplicate inserts are resolved by the unique database index and return the durable winner.

Forwarding uses a deterministic per-target digest derived from the supplied forward key, source message, sender, and target conversation. Repeating the same forward operation does not create a duplicate target message.

`set_message_star` and `set_message_reaction` are desired-state mutations. Retrying a successful request cannot invert the state. Delete-for-me/everyone operations are also idempotent for already-applied state.

## Caching

Chat correctness does not depend on cache state. Redis is used only for shared rate limiting/throttling and presence write/broadcast suppression. `User.last_active` remains the durable presence timestamp.

Presence activity writes are shared-Redis throttled to reduce database write amplification. Presence broadcast fanout is limited to a bounded set of recent active Chat peers and filtered through Social blocking.

## Realtime

Persistent mutations publish only after successful transaction commit through `aos.services.chat.events.publish_after_commit`. Events are targeted to authenticated user rooms; Chat never publishes conversation history to an unauthenticated/global room.

Current Chat events are:

- `aos_new_message`;
- `aos_message_edited`;
- `aos_messages_deleted`;
- `aos_message_reaction_updated`;
- `aos_message_status`;
- `aos_typing`;
- `aos_presence_update`.

Realtime payloads contain public Chat/object IDs and viewer-safe projections. They do not contain database room names, worker identifiers, storage secrets, raw Frappe tracebacks, or privileged internal object fields.

Clients must treat events as hints: de-duplicate by public IDs/state, tolerate out-of-order delivery, and reconcile with `list_conversations` / `list_messages` after reconnect or ambiguity. Delivery/read timestamps are monotonic durable state; stale socket events must not regress them.

## Permissions / Privacy

Every message/conversation operation re-checks authenticated membership against the database. Knowing a conversation or message ID is never sufficient for access.

Important rules:

- `open_conversation` resolves the target through Accounts and rejects self-chat/unavailable/blocked relationships;
- message reads require current conversation membership and viewer-specific visibility;
- removed/hidden conversation state affects listing, not authorization to arbitrary IDs;
- new transient interaction (send, typing, presence, reaction) observes Social blocking;
- replies must reference a valid message in the same authorized conversation;
- edit and delete-for-everyone require message ownership and applicable lifecycle rules;
- system messages cannot be user-created, edited, reacted to, forwarded, or deleted-for-everyone through client mutation paths;
- attachments are authorized through Media plus Chat membership/linkage;
- shared-content visibility is re-evaluated through its owning feature;
- public errors are stable and sanitized; raw exception strings/tracebacks are not returned.

## Errors / Rate Limits

Stable Chat error families include `CHAT_INVALID_REQUEST`, `CHAT_UNKNOWN_FIELD`, `CHAT_INVALID_IDENTIFIER`, `CHAT_INVALID_CURSOR`, `CHAT_INPUT_TOO_LARGE`, `CHAT_NOT_FOUND`, `CHAT_ACCESS_DENIED`, `CHAT_CONFLICT`, `CHAT_INVALID_STATE`, `CHAT_RATE_LIMITED`, `CHAT_DEPENDENCY_UNAVAILABLE`, and `CHAT_INTERNAL_ERROR`. Authentication/account-domain errors may also be returned by their owning hardened boundary.

HTTP semantics: validation/identifier/cursor errors use `422`; oversized input `413`; access denial `403`; not found `404`; conflict/invalid state `409`; rate limiting `429`; dependency unavailable `503`; unexpected internal failure `500`.

Application limits per authenticated user per minute are: open conversation 60, list conversations 120, delete conversation 60, send message 120, forward 60, edit 30, delete messages 60, clear Chat 20, set star 120, list stars 120, set reaction 120, translate 60, list messages 300, mark delivered 600, mark read 600, typing 600, and presence reads 120. Edge/Nginx limits may be stricter.

## Performance / Scalability

The hot-path database indexes are installed by `aos.patches.v1_0.install_chat_indexes`. They cover unique participant pair, viewer conversation activity, message idempotency, one call system message, message history, delivery/read scans, replies, shared entity references, attachment linkage/order, private stars, reactions, and translation cache identity.

Conversation/message/star history uses cursor pagination with deterministic tie-breaking. Reads are bounded to at most 50 conversations, 100 messages, or 100 starred messages per request. Bulk identity, reaction, star, attachment, shared-content, and Media URL projections avoid one-query-per-row patterns. Status and clear-history writes process fixed batches. Presence fanout is bounded to recent peers.

No Chat path intentionally performs an unbounded conversation/message scan, deep offset pagination, naming-series allocation, worker-local synchronization, or sticky-session correctness.

## Testing

Chat tests cover strict validation/public IDs, unknown-field rejection, removed aliases, membership/IDOR, blocking, send/retry idempotency, concurrent creation/send constraints, cursor ordering, unread/delivery/read behavior, edits/deletes/reactions/stars, attachment authorization, shared Ads/Shorts/Live projection, Calls boundary, realtime payload/after-commit behavior, notification integration, translation cache races, cleanup, and source/architecture guards.

Database tests create unique fixture identities and must roll back/clean all Users, Accounts, conversations, messages, Media, notifications, and dependent rows they create. Tests must not depend on execution order.

Run on the target Frappe site:

```bash
bench --site "$SITE" migrate
bench --site "$SITE" run-tests --app aos
```

Repository-level static validation used for Chat can also be run with:

```bash
python -m compileall -q aos
python -m unittest aos.api.chat.tests.test_chat_validation_unit aos.api.chat.tests.test_chat_source_guards -v
python ci/validate_api_documentation.py --check
```

## Operational Invariants

1. Database state is authoritative; realtime/cache loss cannot lose a committed message.
2. One unordered account pair maps to at most one Chat conversation.
3. A user cannot read/mutate a conversation or message without current database-backed authorization.
4. One canonical message send idempotency digest maps to at most one message; the same key cannot silently represent a different payload.
5. One user/message has at most one star and at most one reaction.
6. One public Call ID maps to at most one Chat call-system message.
7. Chat attachments contain only Media references; Media remains the metadata/storage/authorization owner.
8. Shared entity references use owning-feature public IDs and are re-projected at read time.
9. Persistent realtime events are scheduled after commit; clients can always recover by reading durable state.
10. The final schema is for fresh deployment. Historical Chat compatibility aliases, sequence IDs, transitional response shapes, and legacy reconciliation code are not part of this contract.
