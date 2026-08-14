# Notifications API

AOS Notifications is an infrastructure/delivery domain. Business domains remain authoritative for the events that may result in a notification. Notification records and push-delivery work never replace Chat message state, Calls state, Live/Short lifecycle, Social relationships, Ads moderation, Verification decisions, or any other domain state machine.

## Public API v1

The stable public methods remain under `aos.api.v1.notifications`:

| Method | HTTP | Purpose |
| --- | --- | --- |
| `list_notifications` | GET/POST through Frappe method transport | List the authenticated account's inbox using bounded cursor pagination. |
| `mark_notification_read` | POST | Idempotently mark one owned notification read. |
| `mark_all_notifications_read` | POST | Mark all notifications that already exist for the authenticated account read. Notifications created concurrently after the update remain unread. |
| `delete_notification` | POST | Delete one owned inbox notification. |
| `clear_notifications` | POST | Clear owned inbox notifications, optionally by canonical category. |
| `register_push_token` | POST | Register/rotate the authenticated account's Android/iOS/web FCM registration token. |
| `deactivate_push_token` | POST | Deactivate an owned provider token without revealing another account's registration. |

All endpoints require authentication, reject unknown business fields, set private/no-store response headers, and use the existing AOS rate-limit service. Public account identity is serialized as canonical account IDs rather than internal Frappe User/email values.

List pagination is ordered by `(creation DESC, name DESC)`. The `before` cursor is an owned notification ID from the selected category, so equal creation timestamps cannot skip or duplicate rows. Page size is bounded to 50.

There is no standalone unread-count endpoint. Clients derive unread state from the modeled inbox behavior unless another feature-specific contract supplies a count.

## Canonical categories and types

The repository's canonical category contract is centralized in `aos.services.notifications.contracts`.

| Category | Notification types | Current producers |
| --- | --- | --- |
| `communication` | `message`, `missed_call` | Chat, Calls missed-call processing |
| `activity` | `follow`, `new_short`, `short_like`, `short_comment`, `short_mention`, `comment_reply`, `live_started` | Social, Shorts, Live |
| `marketplace` | `ad_approved`, `ad_rejected`, `ad_expired`, `review_received`, `review_approved`, `review_rejected` | Ads moderation/expiry, Reviews |
| `account` | `verification_approved`, `verification_rejected` | Verification |

`short_mention` is part of the existing Shorts producer contract (`aos_short_mention`) and is therefore included in the canonical activity mapping.

The incoming-call signal is intentionally **not** an inbox category. Calls uses the transient `aos_incoming_call` delivery event for native incoming-call handling; the persistent Calls notification remains `missed_call` only.

## Category contracts

Each persistent notification type has a server-owned contract defining its stable event name and the allowed/required scalar payload fields. Unknown fields and nested arbitrary objects are rejected when Notification materializes a new record. Existing legacy rows are serialized through a fail-closed public-payload sanitizer so private/internal fields are never emitted merely because they exist in stored JSON.

Recipient, actor, category/type, event name, notification ID, timestamps, title/body construction, resource identity, dedupe key, and provider options are server controlled. There is no public arbitrary-notification creation API.

Title/body are bounded before persistence/delivery. User-generated preview text is normalized and truncated rather than allowing an oversized Chat/comment/ad preview to invalidate the owning business mutation.

## Business event, intent, inbox record, delivery job, provider delivery

The lifecycle is intentionally separated:

1. A business domain performs an authoritative mutation.
2. That producer calls the centralized Notification service with a server-built notification intent.
3. For a persistent event, Notification materializes `AOS Notification` and creates `AOS Notification Delivery Job` plus `AOS Transactional Outbox` work in the caller-managed database transaction.
4. The transactional outbox dispatches only committed work to the private notification-delivery companion service.
5. The companion worker sends through Firebase Cloud Messaging (FCM) and signs a callback to Frappe.
6. Frappe records the bounded delivery result and deactivates provider tokens reported invalid/unregistered.

A provider/notification failure is isolated from an already-valid business operation. Notification code uses local savepoints where isolation is required and does not issue a broad rollback or own the producer's commit.

## Idempotency and dedupe

Persistent notification types use deterministic event identities where the producer has an authoritative event row/ID (for example a message, follow, like, comment, mention, review, Short, or verification decision). `AOS Notification.dedupe_key` and delivery-job idempotency prevent ordinary producer retries or duplicate outbox execution from materializing duplicate work.

The delivery companion also uses the stable delivery idempotency key and durable job lifecycle. This provides database/queue idempotency, but FCM itself does not provide an exactly-once idempotency primitive. A process crash after a provider accepts a send but before the result is durably recorded can therefore still produce a duplicate provider delivery. Clients must tolerate duplicate notification events.

## Delivery jobs

Canonical Frappe delivery-job states are:

`Queued → Dispatching → Processing → Delivered | Skipped | Failed | Cancelled`

The existing transactional outbox adds claim/lease, retry scheduling, reconciliation, and terminal/dead-letter behavior around dispatch. Companion RQ work uses bounded retry intervals. Retries are not infinite. A transport failure after job submission keeps the Frappe job nonterminal so stable-id reconciliation can determine whether the companion already accepted it.

Before dispatch, Frappe re-checks the recipient's current account availability. Actor-scoped notifications also re-check actor availability and the canonical Social block relationship. Policy uncertainty is treated as privacy-sensitive and suppresses only the notification delivery; it does not rewrite or fail the owning business event.

For transient incoming calls, dispatch also verifies the authoritative Call still exists, belongs to the intended receiver, and remains `initiated`/`ringing`. Notification never changes Call state.

Partial multi-device FCM sends are recorded per batch. Invalid/unregistered token hashes are returned to Frappe and the canonical token rows are deactivated. A partially successful provider send is not blindly retried, because doing so would intentionally duplicate delivery to devices that already succeeded.

## Device registration and privacy

`AOS Push Token` stores Android, iOS, and web FCM registration tokens. Tokens are private provider identifiers and are never returned by normal account/profile/notification serializers or logged in full.

Registration enforces:

- authenticated ownership;
- canonical `android` / `ios` / `web` platform values;
- bounded token/device-ID validation;
- SHA-256 token hashes for lookup and diagnostics;
- one canonical owner for the same provider token;
- token rotation by reusing the canonical user/device registration;
- deactivation of another active registration for the same modeled device when a different signed-in account claims it;
- unique database constraints as the final arbiter for concurrent claims.

Logs use only a short token fingerprint. Delivery diagnostics persist token hashes and safe provider classifications, not raw registration tokens, titles/bodies, arbitrary data payloads, callback credentials, Firebase credentials, or full provider message IDs.

Account deactivation disables the account's provider-token rows and cancels still-deliverable Notification jobs/outbox work while preserving reversible private state. Account deletion physically removes provider-token rows and cancels all still-deliverable Notification jobs/outbox work in bounded batches. Historical terminal delivery audit rows may remain according to the existing recoverable-deletion retention model, but stored delivery request/response diagnostics are privacy-redacted.

## Read/unread concurrency

Read state is per persistent inbox record. Mark-one is conditional/idempotent. Mark-all is a single set-based update over rows that are unread at execution time. A notification committed after that statement remains unread, which avoids losing a newly arriving notification. There is no separate materialized unread counter in the Notification model, so a counter cannot drift or go negative.

## Calls compatibility

Calls keeps its existing time-sensitive signaling contract. `aos_incoming_call` is transient and can use high-priority/data-only Android delivery for the native incoming-call path. Notification hardening does not introduce a generic `call` category, does not replace Calls realtime/LiveKit state, and does not convert incoming-call signaling into a persistent Notification Center record. `missed_call` remains the canonical persistent inbox type.

## Provider integration

The deployed companion uses Firebase Admin / Firebase Cloud Messaging for all modeled platforms, including iOS registrations. There is no separate direct APNs or PushKit adapter in this repository. Provider credentials remain environment/secret-mounted server configuration and are not accepted from public API payloads.

The companion accepts only signed server-to-server jobs, validates a bounded strict schema, supports only the actually implemented `push` channel, and rejects arbitrary extra provider options. FCM data payload size is bounded before provider dispatch.

## Rate limits

Current per-account limits are defined in `aos.api.notifications.constants`:

- register token: 30/minute;
- deactivate token: 30/minute;
- list: 60/minute;
- mark one read: 60/minute;
- mark all read: 30/minute;
- delete one: 60/minute;
- clear: 10/minute.

Internal workers/callbacks use their existing server-to-server authentication and are not exposed as public notification-generation endpoints.

## Unsupported capabilities intentionally not invented

The current repository does **not** model these as Notification product capabilities:

- notification preference/category-preference DocTypes or public preference APIs;
- a standalone unread-count endpoint/materialized unread counter;
- scheduled-notification campaigns/product scheduling;
- notification aggregation/bundling rules beyond existing producer dedupe;
- a direct APNs/PushKit provider adapter (iOS currently uses Firebase Admin/FCM);
- an independent browser Web Push/VAPID provider (the modeled `web` token is delivered through FCM);
- Notification Center realtime events/unread-count broadcasts;
- public arbitrary notification creation;
- email or SMS Notification delivery, even though legacy delivery-job schema choices include those channel labels.

These should be added only through an explicit product/backend contract, not inferred inside Notification infrastructure.
