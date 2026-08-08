# Chat messages

## Message model

A message belongs to one conversation and one sender. Core object references (`conversation`, `sender`, `message_type`, `ad`, `short`, `live`, `reply_to_message`) are immutable after insertion. Delivery/read/edit/delete/forward/idempotency fields are system managed.

`message_type` is derived from the payload by the send core:

- `text`: text only;
- `media`: attachment-only;
- `ad`: native ad reference, optional note;
- `short`: native Short reference, optional note;
- `live`: native Live reference, optional note;
- `mixed`: supported combination of text/reference/media;
- `system`: server-created only.

Only one shared object reference (`ad`, `short` or `live`) may be attached to a normal message.

## Replies

`reply_to_message` must resolve to the same conversation. Reply serialization contains only the visible/public-safe representation of the referenced message. Delete-for-everyone and object-visibility changes are reflected on subsequent history loads.

## Edit

Only the sender may edit a visible, non-deleted editable message. Pure Short/Live messages are not editable; mixed/text/ad behavior preserves the established product contract. Original content and edit timestamp are system managed. The peer receives `aos_message_edited` after commit.

## Delete

- `delete_scope=me` hides the selected messages only for the caller and does not notify the peer.
- `delete_scope=everyone` is sender-only, respects protected system messages, is idempotent and broadcasts `aos_messages_deleted` after commit.
- `clear_chat` is private history hiding for the caller and resets that participant's unread state.
- conversation deletion is a participant-specific soft hide/deactivation, not physical history deletion.

## Forwarding

Forwarding preserves compatible native references and Media attachments, but every target conversation is re-authorized. Short/Live visibility is checked for sender and target recipient. Per-target idempotency prevents duplicate forwarded rows under retries.

## Idempotency

`send_message` and feature-owned share adapters accept a client idempotency key. The raw key is never stored; Chat derives a SHA-256 digest bound to operation, sender and conversation. Database uniqueness is the final concurrent correctness boundary. Duplicate retries return the previously committed message without incrementing unread state or duplicating notification/realtime side effects.

## Unavailable shared objects

Shared-object IDs remain stable historical references, but previews are always re-authorized for the current viewer. If a referenced object can no longer be opened, the message remains in history and the corresponding preview is suppressed:

- `ad_preview: null`, `ad_unavailable: true`
- `short_preview: null`, `short_unavailable: true`
- `live_preview: null`, `live_unavailable: true`

Clients should render an unavailable placeholder and must not infer access from the continued presence of the canonical object ID.
