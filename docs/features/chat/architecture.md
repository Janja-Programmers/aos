# Chat architecture

## Layers

1. **Public v1 wrappers** — `aos/api/v1/chat/__init__.py`. Thin whitelisted methods route every request through the strict Chat boundary.
2. **API boundary** — `aos/services/chat/api.py`, `endpoints.py`, `validation.py`, `errors.py`. It validates request fields and public IDs, establishes operation savepoints and maps failures to stable `CHAT_*` errors.
3. **Domain/service layer** — `aos/services/chat/`. Shared feature adapters use `ChatService`; deterministic lock helpers are in `repository.py`; after-commit realtime helpers are in `events.py`.
4. **Chat implementation** — `aos/api/chat/`. Existing feature modules remain the persistence implementation behind the canonical boundary.
5. **DocTypes** — AOS Conversation, AOS Message, AOS Message Attachment, AOS Message Reaction, AOS Message Star and AOS Message Translation.
6. **Integrations** — Social/blocking, Accounts, Media, Ads, Shorts, Live, Notifications/outbox, seller response metrics and realtime.

## Dependency direction

Feature-owned share endpoints depend on the Chat service, never on Chat table details:

```text
Live.share_to_chat ─┐
                    ├─> ChatService -> canonical send core -> AOS Message
Shorts.share_to_chat┘                               ├-> outbox/notification
                                                   └-> after-commit realtime
```

Chat serializers may query Ads/Shorts/Live only through bounded read/policy helpers. Chat does not duplicate Social relationship rules.

## Lock order

Mutations that need more than one row lock use this deterministic order:

1. conversation rows, sorted by ID;
2. message rows, sorted by ID;
3. dependent star/reaction/attachment rows as required.

This order is used by send, forward, edit, delete, star and reaction paths to reduce deadlock risk.

## Transaction boundary

Public Chat mutations create an operation savepoint. Chat never calls `frappe.db.commit()`. On handled failure only the Chat savepoint is rolled back, and the pre-existing before/after commit/rollback callback lists plus transactional outbox registration state are restored. External translation calls are not performed while broad Chat row locks are held.
