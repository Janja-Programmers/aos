# API contracts

Base path: `/api/method/aos.api.v1.social.<method>`.

All success responses are `{ok:true,message,data}`. Failures are `{ok:false,message,error,data}` with an appropriate HTTP status.

## `toggle_follow` — POST, authenticated

Fields: `account_id` (preferred) or `target_user` (legacy), optional `action` = `toggle|follow|unfollow`. Supplying both aliases is allowed only when they resolve to the same account. Unknown client fields are rejected; Frappe-owned transport metadata such as `cmd` is removed at the public v1 boundary before domain validation.

Response data retains `status`, relationship booleans/status/action label, target/current counters and displays; `changed` indicates whether a row changed.

## `get_relationship_status` — GET, authenticated

Fields: `account_id` or `target_user`. Returns the viewer-relative relationship. Blocked relationships expose only neutral graph state plus the block capability projection.

## `get_following`, `get_followers`, `get_friends` — GET, authenticated

Fields: `limit` 1–50, optional `start`, optional signed `cursor`, optional `search` 2–80 characters. `start` and `cursor` conflict. Response: `items,total,total_display,limit,start,search,has_more,next_cursor`.

`friends` means reciprocal follow edges; it is not a friend-request workflow.

## `search_users` — GET, authenticated

Fields: `query` or legacy `search`, `limit` 1–50, optional `start` or signed `cursor`. Search matches display name, public account ID, first name, and full name; it does not search or return email/User.name. Blocked, disabled, suspended, deactivated, or deleted accounts are excluded.

## `block_user` — POST, authenticated

Fields: `account_id` or `target_user`, optional `reason` up to 300 normalized characters. Idempotently activates one directional block and atomically removes follow edges in both directions.

## `unblock_user` — POST, authenticated

Fields: `account_id` or `target_user`. Idempotently deactivates the viewer’s block. It does not restore prior follows.

## `get_block_status` — GET, authenticated

Fields: `account_id` or `target_user`. Returns block capability fields only.

## `list_blocked_users` — GET, authenticated

Fields: `limit`, optional `start` or signed `cursor`. The `id` is an opaque `BLK-*` reference, not the block document name. Reasons are visible only to the blocker.

## Stable errors

- `SOCIAL_UNKNOWN_FIELD` 422
- `SOCIAL_ALIAS_CONFLICT` 422
- `SOCIAL_TARGET_REQUIRED` 422
- `SOCIAL_INVALID_ACCOUNT_ID` 422
- `SOCIAL_INVALID_ACTION` 422
- `SOCIAL_INVALID_REASON` 422
- `SOCIAL_INVALID_SEARCH` 422
- `SOCIAL_INVALID_LIMIT` / `SOCIAL_INVALID_OFFSET` 422
- `SOCIAL_INVALID_CURSOR` / `SOCIAL_PAGINATION_CONFLICT` 422
- `SOCIAL_SELF_ACTION` 422
- `SOCIAL_ACTOR_UNAVAILABLE` / `SOCIAL_BLOCKED` 403
- `SOCIAL_PROFILE_UNAVAILABLE` 404
- `SOCIAL_CONFLICT` 409
- `SOCIAL_INTERNAL_ERROR` 500
