# Notifications

A newly created follow can create one persistent `follow` notification and one notification-delivery job/outbox record. The client payload contains only the follower’s public `ACC-*` ID. Self-notifications are impossible because self-follow is rejected.

The notification, delivery job, outbox row, and follow edge share the caller transaction. If outbox registration fails, the API rolls back the follow. If notification delivery is administratively disabled, the persistent notification remains valid and no delivery job is expected.

Repeated idempotent follows do not notify. Follow churn within five minutes is deduplicated to reduce amplification. Unfollow, block, and unblock do not currently create notifications, preserving existing product behavior.

Follower-wide Shorts and Live fanout uses a deterministic, active-account, block-aware batch capped at 500 recipients per originating action. This prevents synchronous notification amplification. A durable paginated fanout worker is intentionally deferred for accounts that exceed this cap; the core Social mutation and direct follow notification contracts are unaffected.
