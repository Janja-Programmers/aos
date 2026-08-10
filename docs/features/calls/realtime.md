# Calls realtime and native incoming delivery

Realtime accelerates synchronization; `AOS Call` persisted state is authoritative and recoverable through `get_call_status`.

Existing event names are preserved:

- `aos_incoming_call` — receiver only
- `aos_call_ringing` — caller only
- `aos_call_accepted` — caller only
- `aos_call_rejected` — caller only
- `aos_call_cancelled` — receiver only
- `aos_call_ended` — both participants
- `aos_call_not_answered` — both participants, with receiver-side missed semantics
- `aos_call_video_upgrade_requested`
- `aos_call_video_upgrade_accepted`
- `aos_call_video_upgrade_declined`
- `aos_call_video_upgrade_cancelled`

Every Calls realtime publish uses `after_commit=True`, so rolled-back state cannot produce ghost lifecycle events. Payloads use the shared public serializer and expose public call/conversation IDs, opaque `ACC-*` participant/actor IDs, safe display metadata, call type/state, and authoritative timestamps/duration. Internal User/email IDs, LiveKit JWTs, secrets, and push tokens are not emitted.

## Background / terminated mobile

Incoming delivery is a **transient** notification-delivery/outbox event with event/type `aos_incoming_call`, call ID, call type, public caller identity, and safe caller display metadata. It is intentionally separate from persistent notification-center entries.

For **Android**, the notification-delivery companion sends this one event as high-priority **data-only FCM**, preserves the 30-second TTL, and applies a stable collapse key derived from the public Call ID. It deliberately omits the top-level FCM `notification` block and Android notification-channel options so a background message handler can receive the data and present native ConnectionService/CallKit-style UI. All non-call notifications retain the existing alert+data behavior.

For **iOS/web**, the current push-token model keeps the existing alert+data FCM delivery. This repository does **not** model APNs PushKit/VoIP tokens or a Calls-specific APNs VoIP provider, so it must not claim guaranteed terminated-state iOS native CallKit wake-up. If iOS requires that guarantee, the device/push-token infrastructure must add the platform VoIP-token contract separately; it is not synthesized inside Calls.

A stale native action must first reconcile with `get_call_status`. `can_show_incoming_ui`/`can_accept` are true only for the intended receiver while the call remains active and `initiated`/`ringing`. `get_call_token` refuses a receiver before acceptance and refuses all terminal calls.
