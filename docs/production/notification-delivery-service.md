# AOS Notification Delivery Service

AOS separates business events, in-app Notification Center persistence, foreground realtime synchronization, durable delivery intent, and external provider work. Notification is infrastructure/delivery; it does not own Chat, Calls, Live, Shorts, Social, Ads, Reviews, or Verification business state.

## Ownership

Frappe owns:

- canonical notification category/type contracts;
- `AOS Notification` inbox records and read state;
- derived unread count;
- recipient-scoped post-commit `aos_notification_center` realtime synchronization;
- `AOS Push Token` ownership/rotation/deactivation;
- public Firebase Web Messaging bootstrap configuration;
- `AOS Notification Delivery Job` lifecycle;
- `AOS Transactional Outbox` atomic enqueue/claim/reconciliation;
- recipient/account/block/privacy policy at materialization and again before dispatch;
- provider-result persistence and invalid-registration deactivation.

The private `infra/notification-delivery` companion owns:

- Firebase Admin / Firebase Cloud Messaging provider calls;
- Android/APNs-through-FCM/WebPush-through-FCM provider configuration;
- provider timeout and bounded exponential worker retry;
- batching and per-registration provider classification;
- signed callback to Frappe.

There is no direct APNs/PushKit adapter. Android, iOS, and web targets are delivered by Firebase Admin. AOS supports both legacy FCM registration tokens and Firebase Installation IDs (FIDs); existing rows default to `token`, while new clients can explicitly register `fid`. There is no email/SMS delivery implementation.

## Firebase target migration compatibility

AOS intentionally supports both Firebase target generations during migration:

- `registration_kind=token` uses `messaging.MulticastMessage(tokens=[...])` for existing mobile/web registrations;
- `registration_kind=fid` uses `messaging.MulticastMessage(fids=[...])` for Firebase Installation IDs.

The repository pins `firebase-admin==7.5.0`, which supports both target types. Frappe sends at most 500 active registrations for a recipient in one delivery job. The companion further separates token and FID targets into homogeneous provider calls so Firebase response ordering maps deterministically back to the private registration hash. Existing callers that omit `registration_kind` remain token-based without a migration-time client rollout.

`AOS Push Token.token` remains the legacy database/API field name and stores the opaque identifier for either kind; it is not exposed publicly. `token_hash` remains a SHA-256 privacy-safe lookup/deactivation key across both target kinds.

## End-to-end flow

```text
Authoritative business mutation
→ server-built Notification intent
→ [same DB transaction] AOS Notification (persistent only)
→ [same DB transaction] AOS Notification Delivery Job
→ [same DB transaction] AOS Transactional Outbox
→ register recipient realtime callback for after commit
→ commit
├─→ recipient-scoped aos_notification_center event (foreground hint)
└─→ outbox dispatch to private notification-api
    → notification-worker sends via FCM
    → signed callback to Frappe
    → Frappe marks Delivered / Skipped / Failed and disables invalid tokens
```

The persistent inbox remains authoritative. Realtime and FCM are independent transports: either can fail without rolling back the committed business operation. Active clients reconcile the inbox after reconnect/resume.

Incoming Calls are the intentional transient exception to inbox materialization: `aos_incoming_call` creates transient delivery work only. The dispatch boundary re-checks authoritative Calls state so a delayed job is skipped when the call is no longer `initiated`/`ringing`. `missed_call` remains the persistent Calls notification type. The Notification Center realtime event is never used as incoming-call signaling.

## Foreground realtime

The stable event is `aos_notification_center`, version `1`, recipient-scoped using Frappe's `user=` channel. No room/broadcast channel is used.

Actions:

- `created`: public-safe notification + derived unread count;
- `read`: notification ID + derived unread count;
- `read_all`: derived unread count;
- `deleted`: notification ID + derived unread count;
- `cleared`: category + deleted count + derived unread count.

Callbacks are registered on `frappe.db.after_commit`. A rolled-back business transaction cannot leak a notification over realtime. Duplicate producer retries do not emit duplicate creation events. Immediately before a `created` event is emitted, the callback re-checks recipient/actor account availability and canonical Social block policy, closing the enqueue-to-foreground-delivery privacy race. Publication errors are logged with safe identifiers and never fail the transaction.

## Delivery lifecycle and idempotency

Canonical `AOS Notification Delivery Job` states:

```text
Queued → Dispatching → Processing → Delivered | Skipped | Failed | Cancelled
```

`AOS Transactional Outbox` provides durable claim/lease state, bounded retry scheduling, stale-claim recovery, stable dispatch generations/tokens, and reconciliation. Delivery-job idempotency keys are deterministic: persistent jobs derive identity from the notification record; transient incoming calls use the stable Calls event identity.

The companion uses the same stable idempotency identity and durable work lifecycle. RQ provider retries use bounded exponential intervals, capped at one hour per delay and at the configured retry count. Firebase Admin receives a bounded HTTP timeout through its `httpTimeout` option. Callback HTTP calls use a separately bounded timeout. Poison work does not retry forever.

FCM itself cannot guarantee exactly-once delivery after a crash that occurs after provider acceptance but before durable local result recording. Mobile/web consumers must tolerate duplicate delivery events and reconcile persistent notifications by `notification_id`.

## FCM payload and platform behavior

The data object is bounded independently, and AOS also bounds an estimated notification+data envelope to 3500 bytes after server-owned transport fields are added. This stays conservatively below FCM’s documented 4096-byte token-message payload limit and leaves serialization/platform headroom. Persistent pushes include:

- canonical `event`;
- canonical public `notification_id`;
- canonical `notification_type`;
- only the notification-type contract's public-safe route/resource fields.

Android incoming calls remain data-only, high-priority, short-TTL, and collapsible per call so the native background handler can present CallKit/ConnectionService UI. Normal Android notifications retain alert+data delivery.

For iOS and web registrations delivered through FCM, server-owned priority/TTL are mapped into APNs and WebPush headers. No client may supply arbitrary provider fields.

## Firebase readiness

`GET /ready` succeeds only when Redis is reachable and, unless `NOTIFICATION_DRY_RUN=true`, the Firebase Admin library and local service-account credential file are available and structurally contain `project_id`, `client_email`, and `private_key`. Readiness does not send a provider message.

Production provider success must still be verified with an actual canary device/token after deployment.

## Partial delivery

Frappe bounds one recipient delivery to the 500 most recently used active registrations, matching the provider multicast boundary and preventing pathological registration fanout from creating an unbounded signed request. The companion sends in provider-sized chunks and records success/failure classifications. Fully transient provider failures participate in the configured bounded retry budget. Partial success is terminally recorded instead of resending the entire audience and knowingly duplicating already-successful devices. Invalid/unregistered registration hashes are returned to Frappe and deactivate matching registrations.

## Privacy and security boundaries

The public API cannot choose a recipient/actor or manufacture arbitrary notifications. Persistent payload contracts reject unknown/nested arbitrary fields. Public serialization exposes canonical account IDs and safe actor snapshots rather than internal Frappe User/email identifiers.

Firebase registration tokens and FIDs are sensitive provider identifiers. They are stored server-side, never returned by ordinary serializers, and diagnostics use SHA-256 hashes or short fingerprints. Persisted delivery request diagnostics omit raw tokens, notification title/body, arbitrary data payloads, callback URLs, and credentials. Provider acceptance IDs are hashed by the companion before callback.

Before external dispatch, Frappe re-checks the recipient's account state. Actor-scoped events also re-check actor availability and the canonical Social block relationship. A privacy-policy lookup that cannot be resolved safely suppresses delivery rather than risking disclosure.

Signed callbacks accept only modeled terminal statuses/counts/token hashes. Callback response persistence is allow-listed and bounded; arbitrary provider response objects are not stored.

## Device ownership

Authenticated Firebase registration validates platform, opaque identifier length/structure, registration kind, and modeled device ID. A provider registration has one canonical owner. Identifier rotation reuses the existing canonical row. When a different signed-in account claims the same modeled device, older active registrations for that device are deactivated. Database uniqueness remains the final concurrency arbiter.

Invalid/unregistered Firebase target hashes returned by the worker deactivate matching Frappe registrations, whether the target is a legacy registration token or a FID. Account deactivation disables token rows and cancels pending Notification jobs/outbox work. Account deletion removes token rows and cancels all pending Notification jobs/outbox work in bounded batches.

## Firebase Web Messaging bootstrap

Frappe exposes authenticated `aos.api.v1.notifications.get_push_config`. It is rate-limited and no-store. `NOTIFICATION_WEB_PUSH_ENABLED=false` is the safe default.

When enabled, the following **public client** values must be configured:

```env
NOTIFICATION_FIREBASE_WEB_API_KEY=...
NOTIFICATION_FIREBASE_WEB_AUTH_DOMAIN=...
NOTIFICATION_FIREBASE_WEB_PROJECT_ID=...
NOTIFICATION_FIREBASE_WEB_STORAGE_BUCKET=...
NOTIFICATION_FIREBASE_WEB_MESSAGING_SENDER_ID=...
NOTIFICATION_FIREBASE_WEB_APP_ID=...
NOTIFICATION_FIREBASE_WEB_MEASUREMENT_ID=...
NOTIFICATION_FIREBASE_WEB_VAPID_PUBLIC_KEY=...
```

Only API key, project/sender/app identifiers, optional public Firebase fields, and the VAPID **public** key are returned. Firebase Admin service-account credentials and private keys remain secret-mounted server configuration. Restrict the Firebase Web API key to the intended browser origins/APIs in Google Cloud/Firebase configuration.

The browser frontend remains responsible for permission UX, Firebase service-worker registration, and registration lifecycle. New web code should use Firebase `register()`/`onRegistered()` and upload the resulting FID with `registration_kind=fid`; legacy `getToken()` clients remain supported during migration. The frontend calls AOS's existing authenticated registration/deactivation endpoints in either case.

## Services

```text
notification-redis
notification-api
notification-worker
```

The API is private and should be bound to localhost/private networking. Job submission is signed with the configured Notification service secret. Callback requests are also signed and validated by Frappe.

## Required deployment variables

See `.env.example` for all `NOTIFICATION_*` variables.

Provider hardening controls include:

```env
NOTIFICATION_PROVIDER_MAX_RETRIES=3
NOTIFICATION_PROVIDER_TIMEOUT_SECONDS=20
NOTIFICATION_CALLBACK_HTTP_TIMEOUT_SECONDS=20
```

For real FCM delivery, mount the Firebase service account JSON:

```env
NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH=./secrets/firebase-service-account.json
NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH=/run/secrets/firebase-service-account.json
NOTIFICATION_DRY_RUN=false
```

For smoke testing without contacting FCM:

```env
NOTIFICATION_DRY_RUN=true
```

Dry-run validation proves the companion lifecycle/schema/callback path only; it does not prove real provider credentials or FCM acceptance.

## Migration hardening

`aos.patches.v1_0.harden_notification_subsystem` remains the data-only, idempotent reconciliation patch. `aos.patches.v1_0.install_notification_indexes` remains the following explicit index patch. This delivery/realtime hardening adds no new database schema and requires no additional migration.

## Unsupported notification product features

No Notification preference API/DocType, standalone unread-count endpoint/materialized counter, scheduled-notification campaign model, direct APNs/PushKit adapter, independent browser Web Push provider, or public arbitrary-notification creation API exists. Web browser push is specifically delivered through Firebase Cloud Messaging using the existing `web` token model.
