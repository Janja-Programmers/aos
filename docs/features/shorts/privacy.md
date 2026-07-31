# Privacy and Social integration

Audience values are `everyone`, `followers`, `friends`, and `only_me`.

- Owner access is allowed for management of non-deleted drafts, failed and processing Shorts.
- Guests can access only ready, visible, approved `everyone` Shorts.
- Followers require viewer -> creator follow.
- Friends require mutual follows.
- `only_me` is owner-only.
- Active blocks in either direction deny access.
- Disabled, deleted, suspended, deactivated or restricted creators are excluded publicly.
- Hidden, deleted, flagged or rejected Shorts are excluded publicly.

Direct-ID failures for inaccessible content use the same unavailable/not-found shape to prevent enumeration. The same policy is used for feeds, comments, likes/saves/reposts lists, shares, downloads, events and analytics. Public serializers expose `ACC-*`, never internal User names or emails.
