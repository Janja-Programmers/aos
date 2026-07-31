# Privacy rules

- Public API identity is the opaque `ACC-*` account ID. Internal User names/emails and Social document names are never serialized.
- Social search does not search email/User.name.
- Active blocks are enforced in both directions for discovery and relationship lists.
- Graph booleans are neutralized while blocked so legacy drift cannot reveal mutual/follow state.
- A user who has been blocked cannot view the blocker’s profile through the relationship projection. A blocker may retain the limited ability to identify and unblock their own blocked entry.
- Block reasons are private to the blocker and appear only in `list_blocked_users`.
- Deleted/inactive blocked entries are serialized as unavailable/deleted without historical names or avatars.
- Structured logs never include account IDs, names, emails, search text, reasons, raw cursors, or relationship data.
- Guest access is not enabled for Social endpoints.
