# Live Notifications and Activity

## Supported notification

The inspected canonical notification service supports `live_started`. Co-host workflow status is delivered through targeted realtime/system messages; dedicated persisted co-host notification types are not present and are not invented.

## Live-start fanout

- The start mutation schedules background fanout after commit.
- Followers are read through the canonical Social repository in deterministic pages.
- Disabled/deleted/suspended accounts and bidirectional blocks are excluded by the Social query.
- Fanout uses batches of 100 and the canonical social-event maximum of 500 recipients.
- Existing notification rows are checked to deduplicate the same recipient/host/Live event.
- Actor is never notified about their own action.
- Each notification uses the canonical NotificationService, transactional outbox, and public Live/account payload conventions.
- One delivery failure is isolated and cannot roll back the Live or remaining batch.

## Realtime

The Live implementation emits these event names:

- `aos_live_started`
- `aos_live_ended`
- `aos_live_viewer_count`
- `aos_live_message`
- `aos_live_message_deleted`
- `aos_live_reaction`
- co-host events listed in `cohosts.md`.

Room events are scoped to `live:<LIVE-ID>`. Private workflow events are scoped to Frappe user channels. Every call uses `after_commit=True`; failed socket delivery does not invalidate database state.

## Preferences and deferred notifications

The repository has no Live-specific notification-preference model, recording-ready notification, mention notification, Live-ended notification, or persisted co-host notification taxonomy. Adding them requires a canonical notification schema/product decision.
