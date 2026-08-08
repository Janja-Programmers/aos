# AOS Chat

AOS Chat is the canonical one-to-one messaging domain for authenticated accounts. It owns conversation membership, persistent messages, private media references, replies, forwarding, edit/delete semantics, stars, reactions, translation cache, delivery/read state, typing/presence integration, notifications and realtime delivery.

## Production guarantees

- Public clients use `aos.api.v1.chat.*`; implementation modules remain internal.
- Conversations are unique per unordered participant pair.
- Message mutation uses caller-managed transactions. Chat never commits internally.
- Handled API failures roll back only to an operation savepoint and restore Frappe transaction callback state.
- Persistent realtime events are registered after commit; websocket failure cannot invalidate committed Chat state.
- Message sends and feature-owned shares support bounded idempotency keys.
- Chat message history can contain first-class `ad`, `short`, and `live` references. Clients navigate using the canonical object ID, not by parsing text URLs.
- Short and Live visibility is re-evaluated when messages are serialized; inaccessible objects become unavailable previews without leaking private state.
- Bidirectional Social blocking prevents new direct messaging and suppresses private presence/Live-state leakage.
- Public Chat payloads use canonical `ACC-*` identities rather than internal Frappe User values wherever an account identity is exposed.

## Supported message types

`text`, `media`, `ad`, `short`, `live`, `mixed`, and backend-created `system` messages.

Native Live sharing is feature-owned through `aos.api.v1.live.share_live_to_chat`. Native Shorts sharing remains feature-owned through `aos.api.v1.shorts.share_short_to_chat`. Both call the canonical Chat service for persistence, notification and realtime behavior.

See the companion documents in this directory for exact API, privacy, migration and operations behavior.
