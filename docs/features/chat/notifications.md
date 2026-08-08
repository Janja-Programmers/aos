# Chat notifications

A newly committed message creates the existing `message` notification through `NotificationService.notify_new_message`.

- Notification/outbox persistence occurs in the same caller-managed transaction when notification creation succeeds.
- Notification creation/delivery failure is isolated so it does not roll back the core Chat message.
- Push/outbox delivery remains asynchronous according to the existing notification infrastructure.
- `message_id` is supplied for notification deduplication.
- Actor/self notification is suppressed by the canonical notification service.
- Payload sender fields use canonical public account ID, never the raw internal User value.
- Blocks/account state are enforced before the message can be created, so invalid recipients do not receive Chat notifications.

Native Short and Live shares use the same Chat notification path because their feature adapters delegate message creation to Chat.
