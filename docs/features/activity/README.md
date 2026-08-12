# AOS Activity backend

AOS Activity is the existing private **Activity Center** projection backed by `AOS User Activity`. It records selected user-facing history from hardened owning domains; it is not an audit log and it is never a source of truth for Ads, Shorts, Social, Search, or Live business state.

## Existing supported activity

The repository currently emits exactly these activity types:

- **Ads:** `ad_view`, `ad_wishlist`, `ad_posted`, `ad_report`
- **Shorts:** `short_watch`, `short_like`, `short_comment`, `short_repost`, `short_report`
- **Search:** `user_search`
- **Social:** `user_follow`, `user_block`, `user_report`
- **Live:** `live_host`, `live_join`, `live_comment`

The DocType also contains the existing groups `Reviews`, `Account`, and `Other`, but the current backend has no Activity producers for Reviews or Account. Hardening did not invent any.

## Lifecycle

Activity rows use the existing statuses:

`Active -> Hidden | Cleared`

`Hidden` and `Cleared` are terminal for a row. A later occurrence of the same action may create a fresh `Active` row after the prior row was hidden/cleared. Repeatable active history is coalesced by a server-generated integrity key and database uniqueness constraint.

## Public surface

The authenticated API remains exactly:

- `aos.api.v1.activity.list_activity`
- `aos.api.v1.activity.hide_activity`
- `aos.api.v1.activity.clear_activity`

There is no public Activity creation/update API. Owning backend features record history through `ActivityService`.

## Privacy model

Activity Center is private personalization/history. Public API responses never expose raw Frappe User names, internal DocType names, moderation/report IDs, analytics session/view IDs, or arbitrary stored metadata. Public account/seller identities are canonicalized to opaque `ACC-*` / `SELLER-*` identifiers where identity metadata is intentionally returned.

Account deletion removes the deleted account's own Activity Center history and hides/redacts retained profile and removed-content snapshots owned by other users.

## Intentionally unsupported

The current repository does not emit Activity Center rows for Reviews, Account/Verification changes, Chat, Calls, Notifications, or arbitrary custom events. It has no public create/edit endpoint, no cross-user history endpoint, no activity-sharing workflow, and no separate Activity retention/export feature. Those capabilities were not invented during hardening.

See `api.md`, `security.md`, `migration.md`, `operations.md`, and `testing.md`.
