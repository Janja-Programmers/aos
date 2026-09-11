# Notifications API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `clear_notifications` | POST | Session required | Client |
| `deactivate_push_token` | POST | Session required | Client |
| `delete_notification` | POST | Session required | Client |
| `get_push_config` | GET | Session required | Client |
| `handle_delivery_callback` | POST | Guest allowed | Signed callback |
| `list_notifications` | GET | Session required | Client |
| `mark_all_notifications_read` | POST | Session required | Client |
| `mark_notification_read` | POST | Session required | Client |
| `register_push_token` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Overview

Notifications is the single canonical AOS domain for the private notification inbox, read state, recipient routing, device registrations, durable push orchestration, provider callbacks, delivery retries, deduplication and delivery observability. The former Notification Delivery product/API surface is not a separate feature; delivery is an internal Notifications subdomain.

Originating domains remain authoritative for business state. Notifications must never decide whether a follow exists, an Ad is approved, a verification succeeded, or Media processing succeeded. The producer commits its authoritative mutation first. Notifications owns only the notification intent, persistence, transport and delivery state.

No public arbitrary-notification endpoint exists. Internal producers use `aos.services.notifications.service.NotificationService` and provider-specific code stays behind `aos.services.notifications.delivery` and the private delivery companion.

## Data model

### `AOS Notification`

The user-facing inbox record.

- `user`: internal authenticated `User` ownership key. This value never appears in the public projection; public identities are canonical `ACC-*` Account IDs.
- `type`: canonical notification type registered in `aos.services.notifications.contracts`.
- `title` / `body`: bounded server-generated display copy.
- `actor`: optional internal actor ownership reference. Public serialization resolves this to canonical Account ID/display metadata.
- `payload`: bounded structured JSON validated by the type contract; arbitrary nested blobs are rejected.
- `is_read`: owner-scoped read state.
- `dedupe_key`: optional unique deterministic logical-event identity. Database uniqueness is the concurrency arbiter.

### `AOS Push Token`

Private provider registration state for an authenticated account/device.

- `user`: internal owner.
- `token`: encrypted-at-rest/site-private provider registration identifier as stored by Frappe; never returned by normal APIs or logged.
- `token_hash`: SHA-256 identity used for uniqueness and safe matching.
- `registration_kind`: required, either `token` (FCM registration token) or `fid` (Firebase Installation ID).
- `device_type`: required `android`, `ios`, or `web`.
- `device_id`: required stable application/device identifier used for ownership transfer safety.
- `is_active`: whether the registration may receive delivery.
- `active_device_key`: hidden concurrency key enforcing one active owner for a modeled device.
- `last_used_at`: recency used when bounding recipient fan-out.

Multiple devices per account are supported. Re-registering the same token or device is idempotent. A device claimed by a different signed-in account disables previous active registrations for that device. Invalid provider registrations are disabled by hashed identity after provider response.

### `AOS Notification Delivery Job`

Internal durable delivery state. It is not a public product API.

Important fields include recipient, optional inbox notification, `persistent`/`transient` kind, channel, event, bounded copy/options, unique `idempotency_key`, attempt counters, status, dispatch timestamps, safe request/response diagnostics and aggregate success/failure counts. The recipient and inbox-notification values are stored as identifier snapshots rather than live Frappe Links: new and nonterminal jobs validate that the referenced rows still exist, while existing terminal delivery records may retain those historical identifiers after account/inbox cleanup. This prevents deletion races from dead-lettering already-suppressible work while preserving creation-time integrity. Raw credentials and unrestricted provider error payloads are not persisted.

Canonical states are:

```text
Queued → Dispatching → Processing → Delivered | Skipped | Failed | Cancelled
```

### `AOS Transactional Outbox`

Shared platform outbox used by Notifications for commit-safe delivery handoff. It stores the job reference, deterministic idempotency key, dispatch method, lease/claim state, bounded retry state, dispatch generation/token, callback/reconciliation state and terminal result digest. Notifications reuses this platform primitive rather than creating a second queue architecture.

## Notification lifecycle and transaction boundary

```text
originating feature mutation
→ feature transaction reaches authoritative committed state
→ canonical Notifications integration boundary
→ AOS Notification + Delivery Job + Transactional Outbox
→ outbox dispatch after commit
→ private notification companion
→ FCM
→ signed callback
→ terminal delivery state / invalid-token cleanup
```

For producers that create a notification in the same database transaction, the outbox row is durable but cannot be dispatched until the transaction commits. For workflows such as asynchronous Media processing that explicitly commit their terminal business state first, notification creation is performed in a separate Notifications-owned transaction. In both cases a provider outage cannot roll back the originating business mutation.

Foreground `aos_notification_center` realtime events are also registered only after commit. Realtime is a synchronization hint, not the source of truth; clients reconcile against the inbox after reconnect/resume and tolerate duplicates/out-of-order events.

Incoming Calls are the intentional transient exception: `aos_incoming_call` creates push-only delivery work and no inbox row. The authoritative Calls state is rechecked before dispatch. Missed calls remain persistent notifications.

## Canonical integration contract

Trusted backend producers call the canonical Notifications service; they do not insert `AOS Notification` directly and do not call Firebase/provider code.

A persistent intent supplies a stable notification type plus server-owned recipient, optional actor, structured payload, display copy and deterministic deduplication key where the business event has a stable identity. The type registry maps the intent to its canonical event and payload schema. Recipient/actor fields are internal server references only; public payloads resolve identities to canonical Account IDs.

When integrating any feature:

> Audit all meaningful user-visible state changes in the feature. If a notification is required, use the canonical Notifications integration boundary. Do not directly insert notification records or call providers. Emit/request notifications only after the originating business transaction is safely committed. Use stable event types, recipient authorization, idempotency/deduplication, and structured metadata. If no notification is required, document that decision explicitly.

Provider delivery must never be performed inline inside the originating business transaction.

## Idempotency, deduplication and provider retry

These are separate guarantees:

- **Idempotency**: repeating the same operation with the same identity reaches the same durable result. Notification delivery jobs and outbox work use deterministic unique keys.
- **Deduplication**: duplicate logical notification creation is prevented by `AOS Notification.dedupe_key` uniqueness. Producer retries that race converge on the existing notification and ensure its delivery job/outbox exists.
- **Provider retry**: transient provider failures are retried with a bounded retry budget/backoff. Permanent failures such as invalid registrations are terminal and disable the affected registration.

Delivery callbacks carry dispatch generation/token identities and are handled atomically. Duplicate callbacks are safe, stale generations/token mismatches are rejected through the shared callback state machine, and callbacks cannot manufacture arbitrary provider state.

FCM cannot guarantee exactly-once device presentation if a worker crashes after provider acceptance but before durable result recording. Clients must tolerate duplicate pushes and reconcile persistent events using `notification_id`.

## Canonical notification types

The current type registry is `aos.services.notifications.contracts`.

| Category | Types |
| --- | --- |
| `communication` | `message`, `missed_call` |
| `activity` | `follow`, `new_short`, `short_like`, `short_comment`, `short_mention`, `comment_reply`, `live_started`, `media_processing_completed`, `media_processing_failed` |
| `marketplace` | `ad_approved`, `ad_rejected`, `ad_expired`, `review_received`, `review_approved`, `review_rejected` |
| `account` | `verification_approved`, `verification_rejected` |

Each type allow-lists its scalar payload fields. Unknown fields, arbitrary nested dictionaries/lists and oversized payloads are rejected. Title/body are normalized and bounded before persistence and provider delivery.

## Inbox/read state

All owner APIs derive identity from the authenticated session; no endpoint accepts a recipient/account argument. Queries are owner-scoped in SQL and private responses are `no-store`.

Inbox order is deterministic: `(creation DESC, name DESC)`. `before` is an owned notification ID and forms a keyset cursor. Default page size is 20 and maximum is 50.

Unread count is derived from indexed `(user, is_read, creation)` state instead of a separately mutable counter, avoiding counter races. `mark_notification_read` locks the owned row and is idempotent. `mark_all_notifications_read` uses one indexed set-based update, so notifications committed after that statement remain unread naturally.

`delete_notification` and `clear_notifications` remove only the authenticated owner's rows. They never expose existence of another account's notification through a successful cross-owner mutation.

## Retention

The inbox is a recent-event product surface, not a permanent audit ledger. Retention is configurable and enabled by default:

```text
AOS_NOTIFICATION_INBOX_RETENTION_DAYS=180
AOS_NOTIFICATION_DELIVERY_SUCCESS_RETENTION_DAYS=30
AOS_NOTIFICATION_DELIVERY_FAILURE_RETENTION_DAYS=90
AOS_NOTIFICATION_RETENTION_BATCH_SIZE=1000
AOS_NOTIFICATION_RETENTION_MAX_BATCHES=20
```

Cleanup runs on the scheduler in bounded batches. Inbox cleanup uses the `(creation, name)` retention index. Terminal delivery cleanup is status/completion-time indexed and deletes only jobs whose associated outbox work is also terminal. Queued/dispatching/processing/reconciliation work is never deleted by retention.

Changing inbox retention changes product-visible history and must be treated as a product/operations decision. The defaults bound table growth for a global deployment while retaining substantially more inbox history than delivery diagnostics.

## Device-token lifecycle and security

`register_push_token` requires authentication plus explicit `token`, `registration_kind`, `device_type`, and `device_id`. Unknown fields are rejected. Registration uses narrow row locks, unique token hash/device constraints and bounded deadlock retry; concurrent claims converge on one canonical registration.

`deactivate_push_token` requires the opaque `token` and explicit `registration_kind`. It is intentionally privacy-preserving: a token that does not exist, is already inactive, belongs to another account, or has a different kind returns the same non-revealing success message.

Account deletion removes push tokens and cancels queued/nonterminal notification delivery/outbox work. Restoration does not resurrect deleted registrations; devices must register again. Provider callbacks that arrive after cancellation are handled by the shared terminal/callback state machine rather than reactivating delivery.

Raw tokens, callback secrets, Firebase credentials and private notification content are excluded from operational logs. Diagnostics use hashes/fingerprints and bounded error categories.

## Provider abstraction and asynchronous delivery

The Notifications domain talks to a private delivery companion through the transactional outbox. The companion owns Firebase Admin/FCM specifics. Android, iOS/APNs-through-FCM and web/WebPush-through-FCM options are generated from server-owned configuration. Feature modules never call FCM directly.

The companion validates strict signed job schemas, applies bounded provider and callback HTTP timeouts, chunks delivery to provider limits, classifies transient/permanent failures and uses bounded exponential retry. A recipient is capped at the 500 most recently used active registrations to prevent unbounded fan-out.

Partial success is terminally recorded instead of resending all devices and knowingly duplicating successful deliveries. Invalid/unregistered registration hashes are returned and disabled in Frappe.

Public web push bootstrap returns only Firebase public client configuration and a VAPID **public** key. Admin service-account JSON, private keys and service/callback secrets are never returned.

## Signed provider callback

The only externally HTTP-exposed delivery operation is:

`POST /api/method/aos.api.v1.notifications.handle_delivery_callback`

It is `allow_guest=True` only because the private companion cannot have a browser session. Authentication is the signed raw request body using the configured Notifications callback secret. The callback:

- accepts POST only;
- verifies the signature before trusting payload state;
- rejects unknown fields;
- validates dispatch/job identities and bounded counts;
- delegates replay/generation/token conflict handling to the shared atomic callback state machine;
- accepts only modeled delivery statuses/channels;
- does not expose raw provider exceptions to callers.

This endpoint belongs to Notifications. There is no separate public `notification_delivery` API namespace.

## Public API contract

All public versioned endpoints are under `aos.api.v1.notifications` and all use `aos.api.shared.transport.execute_endpoint`. Frappe transport metadata such as `cmd` is stripped by the shared executor; true unknown client fields reach endpoint validation and are rejected.

All client endpoints require authentication and use the standard AOS response envelope.

| Endpoint | Input | Output/semantics |
| --- | --- | --- |
| `GET get_push_config` | no fields | `{enabled:false}` when disabled/incomplete, otherwise public Firebase client config only. |
| `GET list_notifications` | optional `category` (`all`, `communication`, `activity`, `marketplace`, `account`), optional `limit` 1–50, optional owned `before` cursor | deterministic page, derived unread count, next cursor. |
| `POST mark_notification_read` | required `notification_id` | idempotently marks one owned row read and returns unread count; unknown/cross-owner ID is `NOTIFICATION_NOT_FOUND`. |
| `POST mark_all_notifications_read` | no fields | set-based owner-only mark-all and authoritative unread count. |
| `POST delete_notification` | required `notification_id` | deletes one owned row and returns unread count. |
| `POST clear_notifications` | optional canonical `category` | deletes only owned rows in the selected category and returns deleted/unread counts. |
| `POST register_push_token` | required `token`, `registration_kind`, `device_type`, `device_id` | idempotently registers/rotates device ownership; returns opaque registration row ID only. |
| `POST deactivate_push_token` | required `token`, `registration_kind` | idempotent/non-revealing deactivation. |
| `POST handle_delivery_callback` | signed raw internal callback schema | terminal delivery state result; not a product-client endpoint. |

Stable Notifications errors include `INVALID_NOTIFICATION_INPUT`, `NOTIFICATION_NOT_FOUND`, `DEVICE_TOKEN_INVALID`, `DELIVERY_CALLBACK_INVALID`, shared authorization errors such as `UNAUTHORIZED`, callback conflict codes from the shared callback state machine, and `INTERNAL_ERROR`. Provider stack traces, credentials and raw internal exceptions are never returned.

## Foreground realtime

Recipient-scoped Frappe event: `aos_notification_center`, payload version `1`.

Actions are `created`, `read`, `read_all`, `deleted`, and `cleared`. `created` contains the public-safe notification plus unread count; state actions contain only the minimum identifiers/counts needed for reconciliation. Publication is `user=` scoped and after-commit only.

## Indexes and scale

Manual indexes are installed idempotently on migration and reasserted from `after_migrate` so fresh installs and migrated installs converge:

- inbox owner timeline `(user, creation, name)`;
- category timeline `(user, type, creation, name)`;
- unread lookup `(user, is_read, creation)`;
- inbox retention `(creation, name)`;
- active push registrations `(user, is_active, last_used_at, name)`;
- modeled device lookup `(user, device_id, is_active, name)`;
- unique active-device concurrency key;
- delivery job user/status;
- delivery retry scan `(status, attempt_count, creation, name)`;
- delivery retention `(status, completed_at, name)`.

Notification list serialization bulk-resolves actor display data rather than querying one actor per row. Inbox pagination is keyset based and bounded. Unread count is index-backed and scoped to one recipient. No shared/private cross-user cache is used; current database/index behavior is preferable to introducing a cache-coherency race.

## Observability

Structured notification logs use canonical notification IDs/account IDs where safe, delivery/job identities, status/outcome, retry counts and bounded error categories. Token diagnostics use only hashes/fingerprints. Notification title/body, raw provider payloads, raw tokens and credentials are not emitted merely for debugging.

Operational delivery records preserve enough status/count/timestamp data for retry and incident diagnosis, with retention bounded separately from the user inbox.

## Finalized-feature retrofit audit

This hardening pass audited the five reference features without redesigning their contracts:

| Feature | Decision |
| --- | --- |
| Localization | No notification added. Normal locale/currency/location preference operations are immediate preference changes and do not justify notification noise. |
| Authentication | OTP transport remains Authentication-owned to preserve enumeration, secrecy and timing guarantees. No generic Notification dependency was inserted into OTP flows. |
| Media | Added notifications for asynchronous background-removal terminal success and permanent failure because the user may reasonably leave before completion. Media commits terminal state first; Notification failure/provider outage cannot roll it back. |
| Accounts | No additional generic notification added. Account lifecycle cleanup integrates with Notifications for token/job cancellation, while security/auth messaging remains owned by Authentication. |
| Catalog | No end-user notification added. Catalog is reference/configuration data and ordinary changes should not create notification noise. |

Regression testing for these domains remains authoritative; Notifications integration must be the smallest possible change and may not weaken their transport, validation, authorization, transaction or performance guarantees.

## Account deletion/restore

Account deletion disables/removes device registrations and cancels pending Notification delivery/outbox work. New delivery is suppressed for unavailable/deleted recipients. Inbox/private-data lifecycle follows the Accounts deletion service and documented retention/purge behavior. Restore does not silently reactivate stale push registrations.

Delivery jobs are retained for a bounded operational window and may therefore outlive the User or inbox Notification they originally referenced. Existing jobs that reach `Delivered`, `Skipped`, or `Cancelled` may retain those historical Link values after deletion; terminal-state persistence deliberately ignores only those now-stale links. New and nonterminal jobs still require live User/Notification references. If a queued job discovers that its recipient or inbox row disappeared, it is completed locally as `Skipped` (for example `recipient_missing` or `notification_missing`) rather than retried/dead-lettered.

## Unsupported capabilities

The current product does not include notification preferences/quiet hours, email/SMS notification delivery, public arbitrary notification creation, a materialized unread-counter DocType, scheduled notification campaigns, a direct APNs/PushKit adapter, or an independent browser Web Push provider. These should be added only under an explicit product contract, not inferred during infrastructure hardening.
