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
| `list_notifications` | Any* | Session required | Client |
| `mark_all_notifications_read` | POST | Session required | Client |
| `mark_notification_read` | POST | Session required | Client |
| `register_push_token` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

AOS Notifications is an infrastructure/delivery domain. Business domains remain authoritative for the events that may result in a notification. Notification records, foreground realtime hints, and push-delivery work never replace Chat message state, Calls state, Live/Short lifecycle, Social relationships, Ads moderation, Verification decisions, or any other domain state machine.

## Public API v1

The stable public methods are under `aos.api.v1.notifications`:

| Method | HTTP | Purpose |
| --- | --- | --- |
| `get_push_config` | GET | Return the authenticated client's public Firebase Web Messaging bootstrap configuration when web push is enabled. |
| `list_notifications` | GET/POST through Frappe method transport | List the authenticated account's inbox using bounded cursor pagination and return the current derived unread count. |
| `mark_notification_read` | POST | Idempotently mark one owned notification read and return the current unread count. |
| `mark_all_notifications_read` | POST | Mark all notifications that already exist for the authenticated account read. Notifications committed after the update remain unread. |
| `delete_notification` | POST | Delete one owned inbox notification. |
| `clear_notifications` | POST | Clear owned inbox notifications, optionally by canonical category. |
| `register_push_token` | POST | Register/rotate the authenticated account's Android/iOS/web Firebase target. Existing clients omit `registration_kind` and remain legacy `token`; new clients may send `registration_kind=fid`. |
| `deactivate_push_token` | POST | Deactivate an owned Firebase target. `registration_kind` defaults to legacy `token` and may be `fid`. |

All endpoints require authentication, reject unknown business fields, set private/no-store response headers, and use the existing AOS rate-limit service. Public account identity is serialized as canonical account IDs rather than internal Frappe User/email values.

List pagination is ordered by `(creation DESC, name DESC)`. The `before` cursor is an owned notification ID from the selected category, so equal creation timestamps cannot skip or duplicate rows. Page size is bounded to 50.

There is no separate unread-count endpoint or materialized unread counter. `list_notifications` and successful read/delete/clear mutations return the authoritative derived count, and foreground realtime state events carry the same derived count.

## Foreground realtime contract

Persistent `AOS Notification` rows remain the source of truth. Realtime is only a post-commit foreground synchronization transport for active authenticated sessions.

Frappe publishes one recipient-scoped event:

```text
aos_notification_center
```

Payload version is currently `1`. Supported actions are:

- `created` — contains the public-safe serialized notification and `unread_count`;
- `read` — contains `notification_id` and `unread_count`;
- `read_all` — contains `unread_count`;
- `deleted` — contains `notification_id` and `unread_count`;
- `cleared` — contains the canonical category, `deleted_count`, and `unread_count`.

The event is published with Frappe's recipient `user=` scope only after the database transaction commits. Notification creation that is deduplicated from a producer retry does not emit a second `created` event. Before a `created` realtime event is published, AOS re-checks the recipient account, actor availability, and canonical Social block policy for actor-scoped types. Realtime publication failure or privacy suppression is isolated from the database transaction and external push delivery.

Clients must tolerate duplicate/out-of-order transport events and reconcile the REST inbox after reconnect, tab resume, or any suspected gap. Realtime does not replace inbox pagination and is not used as Calls signaling.

## Firebase Web Messaging bootstrap

`get_push_config` is an authenticated, rate-limited, no-store endpoint. With `NOTIFICATION_WEB_PUSH_ENABLED=false` it returns only `{"enabled": false}`. When enabled and fully configured it returns only Firebase **public client** configuration plus the Web Push VAPID **public** key:

- API key;
- auth domain when configured;
- project ID;
- storage bucket when configured;
- messaging sender ID;
- app ID;
- measurement ID when configured;
- VAPID public key.

It never returns Firebase Admin service-account JSON, private keys, callback/service secrets, provider registration identifiers, or any server credential. Configuration remains server-owned so web clients do not hard-code an independent environment contract.

The frontend still owns browser permission UX, Firebase Messaging initialization, service-worker registration, Firebase registration/FID rotation, and calling the existing authenticated push-token APIs. Backend configuration alone does not grant browser notification permission.

### Firebase registration identity compatibility

`register_push_token` and `deactivate_push_token` keep their historical endpoint names and `token` argument for mobile/web compatibility. The opaque value in `token` is interpreted by the additional `registration_kind` field:

- omitted or `token` — legacy FCM registration token;
- `fid` — Firebase Installation ID (FID).

Existing Android/iOS/mobile clients therefore require no change. New Firebase Web Messaging clients should upload the FID in the existing `token` argument together with `registration_kind=fid`. AOS stores `registration_kind` on the private `AOS Push Token` row and forwards it to the delivery companion. The companion sends legacy identifiers using Firebase Admin `tokens=` and FIDs using `fids=`. A single recipient is still capped at 500 active registrations total, regardless of kind.

AOS pins `firebase-admin==7.5.0`, the first Python Admin release used by this repository that supports `fid`/`fids`. Raw registration identifiers of either kind remain private and are never returned by notification/profile serializers or written to logs.

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

Title/body are bounded before persistence/delivery. User-generated preview text is normalized and truncated rather than allowing an oversized Chat/comment/ad preview to invalidate the owning business mutation. The FCM data object and a conservative estimated notification+data envelope, including server-owned `event`, `notification_id`, and `notification_type`, are bounded before durable delivery work is accepted.

## Business event, intent, inbox record, realtime, delivery job, provider delivery

The lifecycle is intentionally separated:

1. A business domain performs an authoritative mutation.
2. That producer calls the centralized Notification service with a server-built notification intent.
3. For a persistent event, Notification materializes `AOS Notification` and creates `AOS Notification Delivery Job` plus `AOS Transactional Outbox` work in the caller-managed database transaction.
4. A recipient-scoped Notification Center realtime event is registered for **after commit**. It is a foreground hint only.
5. The transactional outbox dispatches only committed work to the private notification-delivery companion service.
6. The companion worker sends through Firebase Cloud Messaging (FCM) and signs a callback to Frappe.
7. Frappe records the bounded delivery result and deactivates Firebase registrations reported invalid/unregistered.

A realtime/provider/notification failure is isolated from an already-valid business operation. Notification code uses local savepoints where isolation is required and does not issue a broad rollback or own the producer's commit.

## Idempotency and dedupe

Persistent notification types use deterministic event identities where the producer has an authoritative event row/ID (for example a message, follow, like, comment, mention, review, Short, or verification decision). `AOS Notification.dedupe_key` and delivery-job idempotency prevent ordinary producer retries or duplicate outbox execution from materializing duplicate work. A deduplicated producer retry also does not emit another realtime `created` event.

The delivery companion uses the stable delivery idempotency key and durable job lifecycle. This provides database/queue idempotency, but FCM itself does not provide an exactly-once idempotency primitive. A process crash after a provider accepts a send but before the result is durably recorded can therefore still produce a duplicate provider delivery. Clients must tolerate duplicate delivery events and reconcile by canonical notification ID where available.

## Delivery jobs

Canonical Frappe delivery-job states are:

`Queued → Dispatching → Processing → Delivered | Skipped | Failed | Cancelled`

The existing transactional outbox adds claim/lease, retry scheduling, reconciliation, and terminal/dead-letter behavior around dispatch. Companion RQ work uses bounded exponential retry intervals and a bounded provider HTTP timeout. Retries are not infinite. A transport failure after job submission keeps the Frappe job nonterminal so stable-id reconciliation can determine whether the companion already accepted it.

Before dispatch, Frappe re-checks the recipient's current account availability. Actor-scoped notifications also re-check actor availability and the canonical Social block relationship. Policy uncertainty is treated as privacy-sensitive and suppresses only the notification delivery; it does not rewrite or fail the owning business event.

For transient incoming calls, dispatch also verifies the authoritative Call still exists, belongs to the intended receiver, and remains `initiated`/`ringing`. Notification never changes Call state.

One recipient delivery is bounded to the 500 most recently used active registrations before the signed companion request is built. Partial multi-device FCM sends are recorded per batch. Invalid/unregistered token hashes are returned to Frappe and the canonical token rows are deactivated. A partially successful provider send is not blindly retried, because doing so would intentionally duplicate delivery to devices that already succeeded.

## Device registration and privacy

`AOS Push Token` stores Android, iOS, and web Firebase target identifiers. Historical rows are `registration_kind=token`; newer clients may store `registration_kind=fid`. Both are private provider identifiers and are never returned by normal account/profile/notification serializers or logged in full.

Registration enforces authenticated ownership, canonical platform values, bounded token/device-ID validation, SHA-256 token hashes for lookup/diagnostics, canonical token ownership, modeled device takeover/rotation semantics, and database uniqueness as the final concurrency arbiter.

Logs use only a short token fingerprint. Delivery diagnostics persist token hashes and safe provider classifications, not raw registration tokens, titles/bodies, arbitrary data payloads, callback credentials, Firebase credentials, or full provider message IDs.

Account deactivation disables provider-token rows and cancels still-deliverable Notification jobs/outbox work while preserving reversible private state. Account deletion physically removes provider-token rows and cancels all still-deliverable Notification jobs/outbox work in bounded batches.

## Read/unread concurrency

Read state is per persistent inbox record. Mark-one is conditional/idempotent. Mark-all is one set-based update over rows that are unread at execution time. A notification committed after that statement remains unread. Because unread count is derived from indexed rows rather than materialized state, it cannot drift or go negative. Realtime mutation messages are generated post-commit from the same derived source.

## Calls compatibility

Calls keeps its existing time-sensitive signaling contract. `aos_incoming_call` is transient and can use high-priority/data-only Android delivery for the native incoming-call path. Notification realtime does not become a competing Calls signaling channel. Notification hardening does not introduce a generic `call` category, does not replace Calls realtime/LiveKit state, and does not convert incoming-call signaling into a persistent Notification Center record. `missed_call` remains the canonical persistent inbox type.

## Provider integration

The deployed companion uses Firebase Admin / Firebase Cloud Messaging for all modeled platforms, including iOS and web registrations. There is no separate direct APNs/PushKit adapter and no independent VAPID provider implementation in this repository.

The companion accepts only signed server-to-server jobs, validates a bounded strict schema, supports only the implemented `push` channel, rejects arbitrary provider options, validates its Firebase dependency/credential file at readiness, applies a bounded Firebase Admin HTTP timeout, and uses bounded exponential worker retry. Android, APNs-through-FCM, and WebPush-through-FCM TTL/priority headers are generated only from server-owned options.

## Rate limits

Current per-account limits are defined in `aos.api.notifications.constants`:

- web push bootstrap config: 30/minute;
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
- an independent browser Web Push provider (web registrations are delivered through Firebase Cloud Messaging);
- public arbitrary notification creation;
- email or SMS Notification delivery, even though legacy delivery-job schema choices include those channel labels.

These should be added only through an explicit product/backend contract, not inferred inside Notification infrastructure.
