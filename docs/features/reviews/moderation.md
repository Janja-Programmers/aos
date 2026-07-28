# Moderation

Review title, comment and attached review images are submitted through the existing Moderation Job and transactional-outbox infrastructure. The job context includes the Ad, rating and `moderation_generation`.

Callbacks use the shared signed callback boundary, timestamp/replay validation, worker correlation and outbox idempotency. Reviews additionally locks the target row and compares the callback generation with the current review generation. A callback for an older edit completes safely without changing the newer content. Duplicate current-generation callbacks are harmless.

Decision mapping:

- allow → `Approved`;
- reject → `Rejected`;
- manual review → remains `Pending`;
- fail-closed service failure → remains `Pending` for the matching generation.

Approval creates a persistent author notification. The initial approval also creates a seller `review_received` notification. Rejection creates a generic author notification. Notification push delivery uses the existing notification-delivery job and transactional outbox. Notification bodies do not include raw review content or provider reasons.

Raw provider payloads, scores, moderator notes and internal reasons remain in internal moderation records. Public and author Review APIs expose only stable product codes and generic guidance.
