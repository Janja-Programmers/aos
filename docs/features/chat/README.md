# AOS Chat

## Overview

AOS Chat is the canonical authenticated messaging domain for direct and group conversations. It owns conversation membership, durable messages, replies, forwarding, edit/delete semantics, per-user visibility, stars, reactions, translation cache, delivery/read state, unread counters, Chat Lock state, typing/presence acceleration, Chat notification requests, and Chat realtime events.

The database is authoritative. Redis, sockets, push notifications, and presence caches accelerate delivery but never become permanent Chat truth. Clients reconcile missed, duplicated, delayed, or out-of-order realtime events through the canonical list endpoints.

Chat supports `direct` and `group` conversations. Group membership is normalized rather than encoded into fixed participant columns. Chat Lock is application-level privacy protection; it is **not end-to-end encryption** and must not be described as such.

## Responsibilities

Chat owns:

- one direct conversation per unordered account pair;
- group metadata, membership, roles, join/leave/remove lifecycle, and ownership transfer;
- message ordering and lifecycle;
- per-member unread, clear/delete-for-me, delivery/read, inbox and lock state;
- replies and private forwarding provenance;
- private stars and one reaction per user/message;
- source-aware translation cache rows;
- Chat-to-Media linkage rows;
- minimal references to Ads, Shorts, Live and Calls;
- Chat-specific notification requests and authenticated per-user realtime events.

Chat does not own authentication, account/profile truth, social blocking, Media storage/authorization, push delivery infrastructure, shared-object lifecycle, Calls state, or LiveKit RTC state.

## Boundaries

- **Authentication** supplies the authenticated Frappe session. Mutations never accept a sender override.
- **Accounts** owns immutable public `ACC-*` account IDs and profile/display projection.
- **Social** owns blocking rules. Direct Chat and group-add operations consume the hardened Social helpers instead of maintaining a competing block graph.
- **Media** owns upload lifecycle, readiness, authorization, filenames, MIME/type, dimensions, duration, URLs and storage. Chat stores only Media linkage and group-avatar references.
- **Notifications** owns durable/in-app/push delivery and retries. Locked-chat message notifications request a private preview and contain no sender identity or message text.
- **Ads** owns `ad_*` public IDs and shareability/projection.
- **Shorts** owns `SHR-*` IDs and visibility/projection.
- **Live** owns `LIVE-*` IDs and lifecycle/projection. A newly shared Live must be shareable; the Live-owned share endpoint additionally requires it to be active.
- **Calls** owns `call_*`, call state, conference membership, room lifecycle and LiveKit. A direct or eligible group Chat may bind to the existing Calls contract, and Calls may project one durable system message per call into that conversation. Chat never owns a second call state machine.
- **Translation service** owns model inference. Chat owns message authorization, request limits, source-aware caching and public serialization.

## Architecture

Public traffic enters `aos.api.v1.chat.*`. These wrappers are deliberately thin and invoke `aos.services.chat.api.run_chat_api`, which performs strict field validation, identifier validation, savepoint-scoped rollback, stable public-error normalization and observability. Domain/use-case code lives under `aos.services.chat`.

Persistent writes use database constraints and deterministic row locking. Conversation rows are the primary Chat serialization point; retry-sensitive message mutations additionally lock message rows. No correctness rule depends on Python globals, process-local locks, sticky sessions or worker-local mutable state.

Persistent realtime publication is registered after commit. A recipient socket event is a hint to fetch/reconcile durable state, not permission to trust the event as state authority.

## Data Model

### AOS Conversation

Shared conversation metadata only. Important fields are `conversation_type`, `direct_key`, `title`, `avatar_media`, `created_by`, `participant_count`, `membership_version`, and shared last-message activity fields. `direct_key` is unique and only populated for `direct` conversations.

### AOS Conversation Participant

Authoritative membership and per-user conversation state: `conversation`, `user`, `role`, `status`, `joined_at`, `visible_from`, `left_at`, `added_by`, `unread_count`, `is_hidden`, `cleared_before`, last-visible-message pointers, `is_locked`, and `lock_changed_at`.

`(conversation, user)` is unique. Active group roles are `owner`, `admin`, and `member`; statuses are `active`, `left`, and `removed`. A newly added/re-added member has `visible_from` reset to the join time and cannot read earlier group history.

### AOS Message

Durable message state: `conversation`, `sender`, `message_type`, `content`, canonical shared-object references, `reply_to_message`, edit/delete state, forwarding provenance, `recipient_count`, and hashed idempotency/request fingerprints. Forwarding provenance is internal and is never serialized publicly.

### AOS Message User State

Sparse per-message/per-user state for `hidden_at`, `delivered_at`, and `read_at`. `(message, user)` is unique. Delete-for-me never mutates the shared message.

### AOS Message Attachment

Linkage only: `message`, `media`, `sort_order`. Media metadata and URLs remain Media-owned. `(message, media)` is unique.

### AOS Message Star / AOS Message Reaction

Private per-user message state. Stars use `(message, user)` uniqueness. Reactions also use `(message, user)` uniqueness, so one user has at most one current reaction per message.

### AOS Message Translation

Authorized translation cache. The unique cache identity is `(message, request_source_language, request_target_language, original_content_hash)`. Canonical provider source/target codes are stored separately from normalized request keys so aliases such as `de` do not collide incorrectly with another requested source language.

### AOS Chat Lock Credential

One optional credential per user. It stores only a slow password hash, version, hidden-mode flag, failure counters/lockout and last verification time. The plaintext secret is never stored. Successful verification creates a random 5-minute token in shared Redis, bound to user and credential version.

## Fields

Public Chat IDs are opaque and multi-node safe:

- conversation: `CONV-` + 32 lowercase hexadecimal characters;
- message: `MSG-` + 32 lowercase hexadecimal characters.

Internal high-write linkage/state IDs also use opaque random IDs (`CP-*`, `MUS-*`, `CMA-*`, `CLC-*`) rather than naming series.

Conversation types: `direct`, `group`.

Group roles: `owner`, `admin`, `member`. Membership statuses: `active`, `left`, `removed`. Groups are bounded to **256 active participants** and require at least two participants including the creator.

Group lifecycle changes are durable history events, not socket-only decorations. Creation, rename/photo changes, add/remove, admin promotion/demotion, ownership transfer, and leave actions insert a `system` message such as “X created the group.” or “X added Y.” New members see the event that added them but cannot read messages from before their `visible_from` join boundary.

Message types: `text`, `media`, `ad`, `short`, `live`, `mixed`, and backend-managed `system`.

Canonical references are Media IDs, Ads public IDs, Shorts `SHR-*`, Live `LIVE-*`, and Calls public `call_*`. Shared entities are projected at read time; unavailable entities remain durable message references but serialize as unavailable rather than stale duplicated snapshots.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `add_group_members` | POST | Session required | Client |
| `clear_chat` | POST | Session required | Client |
| `configure_chat_lock_secret` | POST | Session required | Client |
| `create_group` | POST | Session required | Client |
| `delete_conversation` | POST | Session required | Client |
| `delete_messages` | POST | Session required | Client |
| `edit_message` | POST | Session required | Client |
| `forward_message` | POST | Session required | Client |
| `get_chat_lock_state` | GET | Session required | Client |
| `get_presence` | GET | Session required | Client |
| `leave_group` | POST | Session required | Client |
| `list_conversations` | GET | Session required | Client |
| `list_group_members` | GET | Session required | Client |
| `list_locked_conversations` | GET | Session required | Client |
| `list_messages` | GET | Session required | Client |
| `list_starred_messages` | GET | Session required | Client |
| `mark_delivered` | POST | Session required | Client |
| `mark_read` | POST | Session required | Client |
| `open_conversation` | POST | Session required | Client |
| `remove_chat_lock_secret` | POST | Session required | Client |
| `remove_group_member` | POST | Session required | Client |
| `send_message` | POST | Session required | Client |
| `send_typing_event` | POST | Session required | Client |
| `set_conversation_lock` | POST | Session required | Client |
| `set_group_member_role` | POST | Session required | Client |
| `set_message_reaction` | POST | Session required | Client |
| `set_message_star` | POST | Session required | Client |
| `transfer_group_ownership` | POST | Session required | Client |
| `translate_message` | POST | Session required | Client |
| `update_group` | POST | Session required | Client |
| `verify_chat_lock_secret` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

All routes are `/api/method/aos.api.v1.chat.<method>` and require an authenticated session. Unknown fields fail closed with `CHAT_UNKNOWN_FIELD`; legacy aliases are not accepted.

| Endpoint | Accepted fields | Canonical behavior |
|---|---|---|
| `open_conversation` | `user` | Open/reuse the unique direct conversation for the authenticated account and public `ACC-*` peer. |
| `create_group` | `title`, `participant_ids`, `avatar_media_id` | Create a group; creator becomes owner. `participant_ids` are public Account IDs. |
| `update_group` | `conversation_id`, `title`, `avatar_media_id`, `remove_avatar`, `lock_token` | Owner/admin updates group metadata; Media owns avatar authorization. |
| `add_group_members` | `conversation_id`, `participant_ids`, `lock_token` | Owner/admin adds bounded members. New history visibility starts at join. |
| `remove_group_member` | `conversation_id`, `account_id`, `lock_token` | Owner/admin removes a member subject to role rules. |
| `set_group_member_role` | `conversation_id`, `account_id`, `role`, `lock_token` | Owner/admin changes another non-owner member between `admin` and `member`; admins cannot change their own role. |
| `transfer_group_ownership` | `conversation_id`, `account_id`, `lock_token` | Owner transfers ownership to an active member. |
| `leave_group` | `conversation_id`, `lock_token` | Current member leaves. If owner leaves, ownership transfers deterministically. |
| `list_group_members` | `conversation_id`, `limit`, `cursor`, `lock_token` | Cursor-paginated active member projection using public account identities. |
| `list_conversations` | `limit`, `cursor` | Normal inbox. Locked conversations are excluded. |
| `list_locked_conversations` | `limit`, `cursor`, `lock_token` | Locked inbox. Hidden mode returns an undiscoverable empty list without a valid token. |
| `delete_conversation` | `conversation_id`, `lock_token` | Per-user hide plus history watermark; a later message can resurrect the row without resurrecting old history. |
| `set_conversation_lock` | `conversation_id`, `locked`, `lock_token` | Per-user lock state. Hidden locked chats require a valid token even to unlock. |
| `configure_chat_lock_secret` | `secret`, `current_secret`, `hide_locked_chats` | Create/change secret and hidden mode. Existing secret changes require the current secret. |
| `verify_chat_lock_secret` | `secret` | Verify secret and issue a short-lived `lock_token`. |
| `remove_chat_lock_secret` | `current_secret` | Remove the optional secret; conversation lock flags remain per-user. |
| `get_chat_lock_state` | `lock_token` | Return secret/hidden-mode state. Hidden mode with no token does not expose locked-chat count. |
| `send_message` | `conversation_id`, `content`, `attachments`, `ad`, `short`, `live`, `reply_to_message`, `idempotency_key`, `lock_token` | Send a durable direct/group message. Attachments are only `{media_id}`. |
| `list_messages` | `conversation_id`, `limit`, `cursor`, `lock_token` | Cursor-paginated durable history filtered by membership visibility, clear/delete state and lock authorization. |
| `forward_message` | `message_id`, `target_conversation_ids`, `idempotency_key`, `lock_token` | Forward to up to 20 conversations. Public responses disclose only `is_forwarded`, not source IDs. |
| `edit_message` | `message_id`, `content`, `lock_token` | Sender-only edit of eligible text/mixed messages. |
| `delete_messages` | `message_ids`, `delete_scope`, `lock_token` | Up to 100 IDs. Scope is `me` or sender-authorized `everyone`. |
| `clear_chat` | `conversation_id`, `lock_token` | Advance this member's `cleared_before` watermark and reset unread/preview state. |
| `set_message_star` | `message_id`, `starred`, `lock_token` | Desired-state private star/unstar; retry-safe. |
| `list_starred_messages` | `limit`, `conversation_id`, `cursor`, `lock_token` | Cursor-paginated visible starred messages; hidden locked chats are not discoverable globally. |
| `set_message_reaction` | `message_id`, `emoji`, `lock_token` | Desired-state reaction; empty emoji removes current reaction. |
| `translate_message` | `message_id`, `target_language`, `source_language`, `force_refresh`, `lock_token` | Authorized on-demand translation. Original message is immutable and unread state is untouched. |
| `mark_delivered` | `conversation_id`, `lock_token` | Bounded durable delivery reconciliation; returns `updated_count` and `has_more`. |
| `mark_read` | `conversation_id`, `lock_token` | Bounded durable read reconciliation and unread decrement; returns `updated_count` and `has_more`. |
| `send_typing_event` | `conversation_id`, `is_typing`, `lock_token` | Ephemeral authenticated per-recipient realtime signal. |
| `get_presence` | `conversation_id`, `lock_token` | Current public presence projection for active peers/members. |

List endpoints return `data.items` and opaque `data.next_cursor`. Limits are 1–100. Message history uses deterministic `(creation, message_id)` keyset ordering; conversation lists use per-user activity plus conversation ID; group members and stars have their own versioned cursor kinds. Deep offset pagination is not part of v1.

`send_message` accepts at most 10 attachments. `message_ids` is bounded to 100, `target_conversation_ids` to 20, and new group-member input to 255 (group total remains 256).

## Chat Lock

Lock state belongs to each participant, never to the conversation globally. Other participants are not told that a user locked a chat.

Without a configured secret, a locked conversation is removed from the normal inbox and appears through `list_locked_conversations`; the client may place its device/app authentication gate in front of that surface. Once a user configures a 4–64 character Unicode Chat Lock secret, **every access to any locked conversation or the locked-conversation list requires a valid secret-derived token**, whether hidden mode is enabled or not. Clients must discard that authorization when the user leaves the locked surface/conversation so the next entry requires the secret again. If `hide_locked_chats=1`, the Locked Chats menu/count must not be exposed at all; discovery is only by explicitly submitting the secret through the client search affordance.

The secret is NFC-normalized, slow-hashed with Frappe's password context, never logged/stored plaintext, and has a five-attempt failure threshold followed by a 15-minute verification lockout. Server authorization tokens have a hard 300-second ceiling and live only in shared Redis; the Web client keeps them in memory only and clears them on exit from the locked surface. Changing the secret increments its version, invalidating older tokens.

Locked-chat notifications suppress sender identity and message content. Locked group metadata realtime events are reduced to `{conversation_id, change: "refresh"}`. Hidden conversations return `CHAT_NOT_FOUND` without authorization so possession of an ID does not reveal them.

## Translation

The Frappe backend calls the private AOS translation service through `aos.integrations.ai.translation_client`; clients never call the model container directly. `/translate` and `/languages` require `TRANSLATION_INTERNAL_TOKEN` and fail closed when the token is missing. `/health` and `/ready` remain unauthenticated for loopback/orchestrator probes.

The bundled NLLB/CTranslate2 runtime has bounded request characters, source tokens, decoding length, beam size, inference concurrency and worker thread counts. It uses local model files, a shared CTranslate2 engine, thread-local tokenizers and a bounded semaphore. The container runs as a non-root user and is loopback-bound by default in Docker Compose.

German is explicitly supported: request aliases including `de`, `de-DE`, `deu`, and `deu_Latn` normalize to NLLB `deu_Latn` and serialize with label `German`. Other supported languages are defined centrally in `infra/translation/app/languages.py`.

Chat caches by message, normalized requested source/target, and content hash. Editing a message changes the hash, so stale translation rows are never returned for changed content. The public translation response does not disclose the internal requesting user.

## Cross-feature Dependencies

### Media

Attachments use only `{media_id}` and group avatars use `avatar_media_id`. Media validates readiness/ownership and owns private URL projection. Chat does not persist copied MIME/type/name/dimensions/duration/URL metadata.

### Accounts and Social

Public requests use `ACC-*` account identities; internal Frappe usernames are never accepted as public peer IDs. Direct-message/open-call relationship rules and group additions consume the hardened Social blocking boundary. Membership/IDOR checks remain Chat-owned.

### Ads, Shorts and Live

Chat stores only canonical public IDs and resolves current visibility/projection from the owning feature. A deleted/private/unavailable object does not corrupt history and is shown as unavailable. Native Live sharing additionally enforces active state through the Live-owned endpoint.

### Calls and LiveKit

Calls may bind a direct call to the exact two-member `direct` conversation, or bind a conference call to a `group` conversation only when the call participant set exactly equals the group's active membership. A Chat-bound group call is available only for groups with 3–32 active members, matching the hardened Calls conference maximum; larger Chat groups remain message-only for calling purposes. One `call_id` maps to at most one Chat system message. Call lifecycle, invitations, RTC tokens and LiveKit remain Calls-owned; Chat only supplies conversation context and renders the durable projection.

### Notifications

Message persistence and Notification creation are separate feature responsibilities. Chat supplies a stable message ID for notification deduplication. Locked recipients request generic private-preview notifications.

## Transaction / Concurrency Model

- Conversation creation uses a unique `direct_key` plus database duplicate handling.
- Group membership mutations lock the conversation then deterministic membership rows; owner departure transfers ownership before deactivation.
- Sends lock the conversation, re-read active membership, validate shared content, then insert the message and update per-member inbox/unread state inside the request transaction.
- Client message idempotency is scoped by sender + conversation + operation + raw key, hashed server-side. Exact replay returns the existing durable message. Reusing the key with different canonical content/references returns `409 CHAT_CONFLICT`.
- Stars/reactions are desired-state mutations and use message/conversation locking plus unique constraints.
- Delivery/read reconciliation processes at most 4 batches × 500 messages per request and returns `has_more` for continued reconciliation.
- Database uniqueness is the final arbiter for direct conversations, memberships, message idempotency, Calls projections, message-user state, stars, reactions, attachments, translation cache and lock credentials.
- Chat code does not manually commit. Public transactional wrappers use savepoints and roll back failed mutations without corrupting the caller's broader transaction context.

## Caching

Permanent Chat state is never Redis-only. Redis is used for short-lived Chat-Lock authorization tokens, presence write/broadcast throttles and existing platform rate limiting. Lock tokens are user-bound and version-bound and expire after 300 seconds. Presence persistence remains `User.last_active`; Redis only limits write/fanout frequency.

Translation results are database-cached because they are durable derived data tied to an immutable content hash. Media/shared-object previews are projected from their owning features rather than copied into a Chat cache record.

## Performance / Scalability

The normalized model is designed for HA/multiple Gunicorn workers and roughly 1M+ global users. Hot paths use bounded queries and deterministic keyset pagination. Important indexes cover direct-key uniqueness, per-user inbox ordering, group member/role scans, message history/sender/replies/shared references, user-state read/delivery/hidden scans, attachments, stars, reactions, translation cache and Chat-Lock credential uniqueness.

Conversation serialization batches participant/profile projection. Message serialization batches reactions, stars, receipts, Media URLs and shared-object previews. Presence peer fanout is capped to 200 recently active peers and shared-Redis throttled. Groups are capped at 256 members to keep per-message realtime/notification fanout bounded; this limit must not be raised without redesigning fanout/read-state economics.

No Chat API uses unbounded deep offset scans. Cleanup/account-deactivation work is bounded and preserves group owner invariants.

## Errors and Rate Limits

Stable Chat errors include `CHAT_INVALID_REQUEST`, `CHAT_UNKNOWN_FIELD`, `CHAT_INVALID_IDENTIFIER`, `CHAT_INPUT_TOO_LARGE`, `CHAT_NOT_FOUND`, `CHAT_ACCESS_DENIED`, `CHAT_CONFLICT`, `CHAT_INVALID_STATE`, `CHAT_LOCK_INVALID_SECRET`, `CHAT_LOCK_SECRET_REQUIRED`, `CHAT_LOCK_RATE_LIMITED`, and dependency errors such as `TRANSLATION_UNAVAILABLE`. Hidden locked chats deliberately use `CHAT_NOT_FOUND` rather than an authorization-specific error.

Rate-limit policies are registered centrally in `ci/public-endpoint-rate-limits.json` and validated against every whitelisted endpoint. High-frequency status/typing operations have separate bounded limits from normal send/mutation operations. Clients should debounce typing/presence work and follow `has_more` rather than looping unboundedly.

## Realtime

Durable events are user-targeted and published only after commit. Important event families include new-message hints, message status, edit/delete/reaction changes, group membership/metadata refreshes, typing and presence. Event payloads are intentionally smaller than canonical REST projections. Reconnect recovery is always `list_conversations`/`list_messages` plus status reconciliation, not replay of a socket-owned state machine.

Multiple devices may receive duplicates or observe events out of order. Clients must deduplicate using durable IDs and refetch when version/order is ambiguous.

## Testing

Authoritative Chat coverage includes:

- strict public field/identifier validation and removed aliases;
- source guards for one v1 surface, normalized schema, Calls boundary, post-commit realtime and rate-limit coverage;
- database-backed direct/group membership, IDOR, join-history boundary and role tests;
- idempotent sends and conflicting key reuse;
- per-member unread/read and delete/clear behavior;
- hidden Chat-Lock secret/token behavior and private notification requirements;
- Media/shared Ads/Shorts/Live projection boundaries;
- German/source-aware translation cache behavior;
- translation service auth, normalization, resource bounds and health/readiness behavior;
- cleanup helpers that roll back or remove test-created Users, Accounts, Chat rows, Media, Notifications and external hot state.

Run at minimum:

```bash
python -m unittest \
  aos.api.chat.tests.test_chat_validation_unit \
  aos.api.chat.tests.test_chat_source_guards -v
PYTHONPATH=infra/translation python -m pytest -q infra/translation/tests/test_service.py
python ci/validate_api_documentation.py
python ci/validate_rate_limit_coverage.py .
python ci/validate_doc_paths.py
python ci/validate_repository.py .
bench --site "$SITE" migrate
bench --site "$SITE" run-tests --app aos
```

The database-backed Chat suite is `aos.api.chat.tests.test_chat_database_contracts` and is included in the full AOS test run.

## Operational Invariants

1. The database is Chat truth; sockets, push and Redis are never a third state authority.
2. Every message/conversation read is authorized through active membership and, when hidden, Chat-Lock token authorization.
3. A group must never retain two owners or lose deterministic owner transfer while active members remain.
4. A re-added member cannot see messages from before the new `visible_from` boundary.
5. A locked chat never exposes message/sender preview through Notifications; hidden lock mode never exposes the locked folder/count/content without a valid token.
6. Calls/Live/Shorts/Ads/Media remain authoritative for their own entities; Chat stores only canonical references.
7. Translation model inference is private, authenticated and bounded; German (`de` → `deu_Latn`) is part of the supported production language registry.
8. No client should depend on internal Frappe usernames, DocType names, room names, Redis keys, forwarding source IDs, model internals or worker details.
