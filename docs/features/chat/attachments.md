# Chat attachments

Chat attachments use the canonical Media subsystem and private `chat_attachment` media objects.

## Rules

- Maximum 10 attachments per message.
- Attachment requests accept the established `media`, `media_id` or `id` aliases only when they resolve to the same canonical `MEDIA-*` ID.
- Nested unknown fields fail closed.
- Media purpose must be `chat_attachment`.
- Media visibility must be Private.
- Media must be Uploaded/Attached and authorized for the sender/conversation.
- The Chat attachment child row stores the Media reference; signed/private access is generated through Media on serialization.
- Duplicate `(message, media)` rows are reconciled by the migration and prevented by a database uniqueness index.
- Attachment type is restricted to image, video, audio or document.

Chat does not mint public URLs for private attachments and does not bypass Media ownership/access rules.
