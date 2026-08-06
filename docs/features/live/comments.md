# Live Comments and Replies

## Persistence and visibility

Comments and replies are persisted in `AOS Live Message`. A successful mutation serializes the committed row and publishes it through `aos_live_message` to the Live room with `after_commit=True`. Broadcast failure cannot roll back the committed comment.

Reconnect recovery uses `list_live_messages` and `list_live_replies`; comments do not depend solely on transient socket memory.

## Authorization

- Reading is guest-accessible only when the Live is accessible under the same account/block policy used by detail/feed/token paths.
- Writing requires login.
- Host can write without a viewer row.
- Non-host author must own the active viewer session.
- Author may soft-delete their own active comment/reply.
- Host may soft-delete comments on their active Live.
- There is no persisted moderator role in the inspected product.

## Validation

- Author, role, timestamps, status, visibility, counts, and IDs are server-controlled.
- Content is NFC-normalized, trimmed, non-empty, NUL-free, and at most 500 characters.
- Content is HTML-escaped before storage and event serialization.
- Optional idempotency key is at most 128 characters.
- `active_idempotency_key` plus database uniqueness is the final duplicate-submission boundary.

## Ordering and pagination

Messages and replies order by `creation ASC, name ASC`. New clients use an HMAC-signed cursor containing endpoint kind, Live/parent scope, creation, and row ID. A cursor cannot be reused across Live streams or between messages and replies. Page reads are bounded.

## Deletion

Deletion is a soft state change. Listing queries require active status, so a deleted comment cannot reappear after reconnect. `aos_live_message_deleted` notifies the room after commit.

## Existing limitations

The data model does not contain a Live-level comments-enabled switch, restricted-word service binding, mention model, report-comment API, or moderation evidence workflow. Those are intentionally not invented by this hardening.
