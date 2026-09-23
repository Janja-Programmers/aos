# Calls realtime and notifications

Database state is authoritative. All Calls realtime publishes use `after_commit=True` and carry opaque Calls/Accounts identities plus monotonic `state_version`.

Core events include `aos_call_ready`, `aos_incoming_call`, participant ringing/joined/declined/left/missed events, `aos_call_cancelled`, `aos_call_ended`, and direct-call video-upgrade events. Payloads include the participant roster projection and current participant state, but never internal User names, Frappe Call names, room names, JWTs, or provider secrets.

Incoming call push is transient through hardened Notifications; missed-call notification is persistent. Every invited participant has an independent durable `ring_expires_at`. Delivery suppression revalidates the recipient participant row and the actual inviter (including a non-initiator who adds somebody to an ongoing group) before delivery, preventing stale or newly-blocked invites.

Multi-device retries converge through row-locked/idempotent mutations. Reconnects reconcile through `get_call_status` and `get_call_token`; for a pending direct call, status reconciliation may idempotently terminalize an invite whose durable `ring_expires_at` has already elapsed instead of waiting for the next minute-scheduler tick. Stale realtime/native actions cannot revive terminal or expired participant state.
