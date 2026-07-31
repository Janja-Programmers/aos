# Notifications and activity

Short interaction notifications continue through `NotificationService`, whose delivery path uses the transactional outbox. Activity history is secondary and never causes the primary mutation to fail.

Rules:

- no self-notification;
- no blocked/inactive recipient notification;
- mention persistence uses internal identity only inside the transaction and returns `ACC-*` publicly;
- notifications are emitted only after the domain mutation succeeds;
- repeated interaction events are deduplicated before counter/fanout work;
- ranking/analytics jobs use `enqueue_after_commit=True`.

No unbounded synchronous follower-wide fanout was added. Existing new-Short fanout remains governed by the notification delivery/outbox infrastructure and deployment rate limits.
