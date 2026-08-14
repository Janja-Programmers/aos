# AOS Notification Delivery Service

AOS separates business events, in-app Notification Center persistence, durable delivery intent, and external provider work. Notification is infrastructure/delivery; it does not own Chat, Calls, Live, Shorts, Social, Ads, Reviews, or Verification business state.

## Ownership

Frappe owns:

- canonical notification category/type contracts;
- `AOS Notification` inbox records and read state;
- `AOS Push Token` ownership/rotation/deactivation;
- `AOS Notification Delivery Job` lifecycle;
- `AOS Transactional Outbox` atomic enqueue/claim/reconciliation;
- recipient/account/block/privacy policy at materialization and again before dispatch;
- provider-result persistence and invalid-token deactivation.

The private `infra/notification-delivery` companion owns:

- Firebase Admin / Firebase Cloud Messaging provider calls;
- batching and per-token provider classification;
- bounded worker retry;
- signed callback to Frappe.

There is no direct APNs/PushKit adapter. Android, iOS, and web registration tokens are delivered by the Firebase Admin integration. There is no email/SMS delivery implementation.

## End-to-end flow

```text
Authoritative business mutation
→ server-built Notification intent
→ [same DB transaction] AOS Notification (persistent only)
→ [same DB transaction] AOS Notification Delivery Job
→ [same DB transaction] AOS Transactional Outbox
→ commit
→ outbox dispatch to private notification-api
→ notification-worker sends via FCM
→ signed callback to Frappe
→ Frappe marks Delivered / Skipped / Failed and disables invalid tokens
```

Provider delivery is never attempted from a public client request and is not performed while the owning business mutation is holding its broad transaction locks. An already-valid business mutation is not rolled back because push infrastructure fails.

Incoming Calls are the intentional transient exception to inbox materialization: the existing `aos_incoming_call` event creates transient delivery work only. The dispatch boundary re-checks authoritative Calls state so a delayed job is skipped when the call is no longer `initiated`/`ringing`. `missed_call` remains the persistent Calls notification type.

## Delivery lifecycle and idempotency

Canonical `AOS Notification Delivery Job` states:

```text
Queued → Dispatching → Processing → Delivered | Skipped | Failed | Cancelled
```

`AOS Transactional Outbox` provides durable claim/lease state, bounded retry scheduling, stale-claim recovery, stable dispatch generations/tokens, and reconciliation. Delivery-job idempotency keys are deterministic: persistent jobs derive identity from the notification record; transient incoming calls use the stable Calls event identity.

The companion uses the same stable idempotency identity and a durable work lifecycle. RQ retry intervals are bounded; poison work does not retry forever. A Frappe transport error after submission leaves the job nonterminal so reconciliation can determine whether the companion already accepted it rather than blindly creating another job.

FCM itself cannot guarantee exactly-once delivery after a crash that occurs after provider acceptance but before durable local result recording. Mobile/web consumers must tolerate duplicate delivery events.

## Current persistent categories

- communication: `message`, `missed_call`
- activity: `follow`, `new_short`, `short_like`, `short_comment`, `short_mention`, `comment_reply`, `live_started`
- marketplace: `ad_approved`, `ad_rejected`, `ad_expired`, `review_received`, `review_approved`, `review_rejected`
- account: `verification_approved`, `verification_rejected`

Category payload schemas/event names are centralized in `aos.services.notifications.contracts`. The transient Calls event `aos_incoming_call` is deliberately outside this persistent category list.

## Privacy and security boundaries

The public API cannot choose a recipient/actor or manufacture arbitrary notifications. Persistent payload contracts reject unknown/nested arbitrary fields. Public serialization exposes canonical account IDs and safe actor snapshots rather than internal Frappe User/email identifiers.

Push tokens are sensitive provider identifiers. They are stored server-side, never returned by ordinary serializers, and diagnostics use SHA-256 hashes or short fingerprints. Persisted delivery request diagnostics omit raw tokens, notification title/body, arbitrary data payloads, callback URLs, and credentials. Provider acceptance IDs are hashed by the companion before callback.

Before external dispatch, Frappe re-checks the recipient's account state. Actor-scoped events also re-check actor availability and the canonical Social block relationship. A privacy-policy lookup that cannot be resolved safely suppresses delivery rather than risking disclosure.

Signed callbacks accept only the modeled terminal statuses/counts/token hashes. Callback response persistence is allow-listed and bounded; arbitrary provider response objects are not stored.

## Device ownership

Authenticated token registration validates platform, token length/structure, and modeled device ID. A provider token has one canonical owner. Token rotation reuses the existing canonical row. When a different signed-in account claims the same modeled device, older active registrations for that device are deactivated. Database uniqueness remains the final concurrency arbiter.

Invalid/unregistered FCM token hashes returned by the worker deactivate the matching Frappe registrations. Account deactivation disables token rows and cancels pending Notification jobs/outbox work. Account deletion removes token rows and cancels all pending Notification jobs/outbox work in bounded batches.

## Partial delivery

The companion sends in provider-sized chunks and records success/failure classifications. Fully transient failures may use the existing bounded worker retry. Partial success is terminally recorded instead of resending the entire audience and knowingly duplicating already-successful devices.

## Services

```text
notification-redis
notification-api
notification-worker
```

The API is private and should be bound to localhost/private networking. Job submission is signed with the configured Notification service secret. Callback requests are also signed and validated by Frappe.

## Required deployment variables

See `.env.example` for all `NOTIFICATION_*` variables.

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

`aos.patches.v1_0.harden_notification_subsystem` is a data-only, idempotent reconciliation patch. It normalizes inbox read state, deactivates provider registrations for unavailable accounts, cancels undeliverable pending jobs/outbox work, and redacts legacy persisted delivery request/response diagnostics in bounded batches.

`aos.patches.v1_0.install_notification_indexes` runs after model/data reconciliation and installs only the indexes justified by the inbox, token, and worker query patterns. This ordering intentionally avoids coupling data reconciliation to schema reload/manual-index creation.

## Unsupported notification product features

No Notification preference API/DocType, standalone unread-count endpoint, scheduled-notification campaign model, direct APNs/PushKit adapter, independent Web Push/VAPID adapter, Notification Center realtime unread broadcast, or public arbitrary-notification creation API exists in this repository. These are intentionally not synthesized by the delivery service.
