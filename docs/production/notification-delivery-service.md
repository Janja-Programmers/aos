# AOS Notification Delivery Service

Phase 4 separates push delivery from Frappe request handling.

## Ownership

Frappe owns:

- `AOS Notification` records
- `AOS Push Token` records
- `AOS Notification Delivery Job` records
- business rules about who receives notifications
- read/unread notification state

The external notification delivery service owns:

- provider calls, currently Firebase Cloud Messaging
- per-token provider response handling
- worker-level retries
- delivery callback to Frappe

## Flow

```text
Frappe business event
→ create AOS Notification if persistent
→ create AOS Notification Delivery Job
→ Frappe queue dispatches job to notification-api
→ notification-worker sends push through FCM
→ notification-worker calls signed Frappe callback
→ Frappe marks job Delivered / Skipped / Failed
→ Frappe deactivates invalid push tokens reported by the worker
```

## Services

```text
notification-redis
notification-api
notification-worker
```

The API is private and should be bound to localhost on the host.

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
