# AOS Activity backend

## Overview

AOS Activity is the authenticated user's private **Activity Center**: a bounded durable read model of selected successful AOS domain actions. It is not an audit ledger, analytics/telemetry store, notification database, public social feed, or source of truth for another feature.

Activity owns its private history rows, canonical event taxonomy, opaque Activity IDs, dedupe identities, hide/clear state, retention, cursor pagination, privacy-safe serialization, and current-resource projection. The owning hardened domains remain authoritative for resource state and authorization.

## Ownership

Activity stores the owner internally, event group/type, first/latest occurrence timestamps, an occurrence count for coalesced events, an internal target reference, a canonical public navigation ID, a bounded presentation snapshot, typed allowlisted metadata, and hidden integrity keys.

Activity derives current availability at read time. It does **not** own Accounts identity, Social relationships/blocks, Seller state, Ad state, Shorts lifecycle/moderation/audience, Live lifecycle/messages, Media, Calls, Chat, Reviews, Notifications, Reports, or analytics. It never mutates those domains to make an Activity row valid.

## Responsibilities

Activity is responsible for:

- accepting only server-side producer calls after an authoritative domain operation succeeds;
- validating a fixed event taxonomy and typed bounded metadata;
- storing at most one self-owned row per emitted producer action, with database-enforced retry identities;
- returning only the current session owner's rows;
- rechecking current resource/account/privacy state before exposing stored snapshots or navigation IDs;
- deterministic bounded keyset pagination, bounded clear operations, and bounded retention cleanup.

## Boundaries

There is no public Activity-create API and no API accepts an owner/account ID. Clients cannot claim that a follow, report, Ad action, Live action, Call, or message occurred. Activity does not fan out to followers or audiences and does not create Notifications.

Calls and Chat already own durable history and therefore have no Activity event types. Reviews, Verification, Saved Searches, and Notifications likewise have no Activity producers in the current product. Reports/Moderation are not redesigned here; an existing successful report operation may provide a hidden producer event identity to Activity.

## Architecture

```text
Accounts / Social / Ads / Shorts / Live / existing report paths
                         ↓
          successful authoritative mutation/read event
                         ↓
        best-effort savepoint-isolated producer hook
                         ↓
                ActivityService
   taxonomy + typed metadata + DB-backed dedupe
                         ↓
                 AOS User Activity
                         ↓
        current-domain batched visibility projection
                         ↓
       private cursor-paginated Activity API
```

Activity writes participate in the caller's transaction and never commit independently. Producer hooks wrap optional Activity work in a savepoint: an Activity failure rolls back only Activity work; if the owning transaction later rolls back, the Activity write rolls back with it.

## Producers

| Producer | Integration boundary | Current events |
|---|---|---|
| Ads / Wishlist | `aos.api.ads.activity` | `ad_view`, `ad_wishlist`, `ad_posted`, `ad_report` |
| Shorts / existing report path | `aos.services.shorts.activity` | `short_report` |
| Search / Social | `aos.api.social.activity` | `user_search`, `user_follow`, `user_block`, `user_report` |
| Live | `aos.api.live.activity` | `live_host`, `live_join`, `live_comment` |

Every producer calls the shared `ActivityService`; none inserts `AOS User Activity` directly. One producer call creates/updates at most one row for the acting account.

## Event Taxonomy

Metadata is an explicit typed schema. Unknown keys, wrong scalar types, malformed public identities, missing required keys, metadata above 16 keys, text above 500 characters, or encoded metadata above 8 KiB are rejected.

| Event | Group | Mode | Target / public route | Metadata schema | Lifecycle rule |
|---|---|---|---|---|---|
| `ad_view` | Ads | coalesce | `AOS Ad` / Ad `public_id` | optional `seller:seller_id`, `category/location/country/ad_status/price_type/currency:text`, `price:number` | public Ad visibility rechecked |
| `ad_wishlist` | Ads | coalesce | `AOS Ad` / Ad `public_id` | same Ad snapshot | wishlist removal hides active row; Ad visibility rechecked |
| `ad_posted` | Ads | once | `AOS Ad` / Ad `public_id` | same Ad snapshot | only owner's current Ad remains navigable |
| `ad_report` | Ads | once | `AOS Ad` / Ad `public_id` | optional `reason:text` | report ID remains hidden; Ad visibility rechecked |
| `short_report` | Shorts | once | `AOS Short` / `SHR-*` | optional `reason:text` | Shorts visibility/moderation/audience rechecked |
| `user_search` | Search | coalesce | no domain target / normalized query | required `query:text`; optional `result_count:int>=0` | private owner history only |
| `user_follow` | Social | coalesce | internal User / `ACC-*` | required `target_user:account_id` | Accounts + Social privacy rechecked |
| `user_block` | Social | coalesce | internal User / `ACC-*` | required `target_user:account_id`; optional `reason:text` | own block remains navigable only while that block is still active and the target has not also blocked the viewer |
| `user_report` | Social | once | internal User / `ACC-*` | required `target_user:account_id`; optional `reason:text` | profile privacy rechecked; report identity hidden |
| `live_host` | Live | once | `AOS Live Stream` / `LIVE-*` | required `live_id:text`, `host_user:account_id` | follows Live's canonical read access; ended stream may remain historical/navigable |
| `live_join` | Live | coalesce | `AOS Live Stream` / `LIVE-*` | required `live_id:text`, `host_user:account_id` | follows Live read access; analytics session/view IDs are never stored |
| `live_comment` | Live | once | `AOS Live Message` / parent `LIVE-*` route | required `live_id:text`, `host_user:account_id`, `is_reply:bool`; optional `comment_preview:text` | stream access plus current message visibility/status rechecked |

`coalesce` updates one active owner/logical-resource row. `once` records one logical producer event once during retention, even after the row is Hidden/Cleared. Dead Shorts watch/like/comment/repost Activity helpers are removed; Shorts analytics remains in Shorts/Analytics.

## Data Model

`AOS User Activity` is the only Activity DocType.

## Fields

- `user`: internal owner; never accepted from Activity public APIs.
- `public_id`: unique opaque `ACT-<32 lowercase hex>` public identity.
- `activity_group`, `activity_type`: canonical taxonomy.
- `status`: `Active`, `Hidden`, `Cleared`; Hidden/Cleared are terminal for that row.
- `occurred_at`, `last_occurrence_at`: first/latest occurrence; latest is the timeline key.
- `count`: coalesced occurrence count capped at 2,147,483,647.
- `target_doctype`, `target_name`: immutable internal resource identity, never serialized.
- `target_title`, `target_subtitle`, `target_image`: bounded presentation snapshot returned only while current access remains valid.
- `route_type`, `route_id`: immutable canonical public navigation target.
- `metadata_json`: typed bounded per-event metadata.
- `unique_key`: immutable hidden SHA-256 logical producer identity; plaintext producer/internal IDs are not retained.
- `active_key`: SHA-256 DB uniqueness key for an active coalesced row.
- `event_key`: SHA-256 DB uniqueness key for one-off retry protection during retention.

Indexes are query-driven:

- unique `public_id` field index;
- `uq_aos_activity_active (active_key)`;
- `uq_aos_activity_event (event_key)`;
- timeline indexes for `(user,status[,group/type],last_occurrence_at,creation,public_id)`;
- lifecycle lookup `idx_aos_activity_route_target (route_type,route_id,status,user)`;
- retention scan `idx_aos_activity_retention (last_occurrence_at,name)`.

There is no actor/follower/recipient feed index because Activity has no such query or fan-out model.

## Naming

Public Activity IDs use `new_prefixed_name("ACT")` and are always generated server-side in `before_insert`; producer input cannot select them. They are opaque, random, stable and multi-worker safe. The internal Frappe row `name` is never returned.

Public resource references use their owning domain's canonical public identity: Ad `public_id`, `ACC-*`, `SHR-*`, and `LIVE-*`. Internal User/Seller/Ad/report/message names remain server-side.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `clear_activity` | POST | Session required | Client |
| `hide_activity` | POST | Session required | Client |
| `list_activity` | GET | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

| Endpoint | HTTP | Accepted fields | Behavior |
|---|---|---|---|
| `aos.api.v1.activity.list_activity` | GET | `limit`, `cursor`, `group`, `type` | current user's active private timeline |
| `aos.api.v1.activity.hide_activity` | POST | `activity_id` | idempotently hides one owner-scoped `ACT-*` row |
| `aos.api.v1.activity.clear_activity` | POST | `group`, `type` | clears one bounded owner-scoped batch |

Unknown fields are rejected. Group/type values are allowlisted and a type must belong to the selected group. There is no sort SQL surface and no public create/update endpoint. All three endpoints require login, use shared AOS rate limiting, and set private/no-store semantics. Current per-user limits are 120 list, 60 hide, and 20 clear calls/minute.

A list item exposes only public Activity semantics: `ACT-*` id, group/type, status, `resource_available`, safe target `{type,id,title,subtitle,image}`, allowlisted metadata, timestamps and count. Internal target DocTypes/names and dedupe keys are never serialized.

## Visibility / Privacy

All reads are owner-scoped server-side: `user = current_session_user`. Changing `activity_id`, target IDs, cursor values, or filters cannot retrieve another account's rows.

One page is projected in batches against authoritative boundaries: Accounts current identity/state, Social capability projection, Ads public/owner visibility, Shorts visibility policy, Live account/block/message access, and Media URLs supplied by owning domains. Projection failures fail closed by resource class.

If a resource is unavailable, Activity returns only an unavailable marker and removes stale title/subtitle/image, navigation ID and metadata. Activity never exposes another user's wishlist/search/report/call/chat/notification history.

## Resource Lifecycle

Stored snapshots are presentation history, not content archives:

- unavailable/deleted/non-public Ad -> unavailable marker;
- hidden/deleted/moderated/audience-ineligible Short -> unavailable marker;
- disabled/deleted/blocked profile -> unavailable marker; an owner-created block event remains navigable only while Social says the owner still blocks the target and the target has not also blocked the owner;
- ended Live stream -> **not automatically unavailable**; Live's canonical `get_live` access permits historical ended streams subject to account/block access, so Activity follows that contract;
- deleted/hidden/inaccessible Live message -> its `live_comment` item becomes unavailable.

If a recoverable resource/account later becomes available again and the Activity row remains Active within retention, the read projection may become available again. Activity never restores or recalculates owning-domain state.

## Account Lifecycle

Recoverable account deletion preserves durable Activity during the restoration window, consistent with Accounts policy; the deleted account cannot authenticate and its profile/content projections fail closed. Restoration reuses preserved history subject to current resource access.

Permanent purge uses `account_purge_service`: owner-private Activity is deleted in bounded batches; retained rows owned by other users are hidden and stripped of target DocType/name, route IDs, metadata and dedupe identities when they reference permanently removed profile/authored content. Activity does not invent a separate deletion policy.

## Transactions / Idempotency

Activity never calls `commit()`. Producer hooks use caller-owned savepoints. An Activity failure rolls back only the optional projection work; an owning transaction rollback also rolls back Activity, so history is never published for a business mutation that did not commit.

Producer logical identities are normalized to one-way SHA-256 before persistence, so internal User/resource/report identifiers are not retained in plaintext dedupe fields. Database uniqueness is the final retry/concurrency boundary:

- coalesced `active_key = SHA-256(user + unit-separator + unique_key)` with `uq_aos_activity_active`;
- once-only `event_key = SHA-256("once" + unit-separator + user + unit-separator + unique_key)` with `uq_aos_activity_event`.

The normal first-insert path does not take a gap lock; concurrent inserts race against the unique index and duplicate-key losers re-read/update the canonical row. Existing coalesced rows are row-locked before counter/snapshot updates. No Python global, local lock, sticky session or single scheduler provides correctness.

## Transaction / Concurrency Model

Activity is a transactionally coupled but non-authoritative read model: same DB transaction as producer, isolated by savepoint on optional failure, no external publish, no independent commit. Hide/clear lock affected owner rows. Public IDs are random and unique; logical retry keys are DB-enforced. Multi-node workers can safely race on the same logical event.

## Fan-out / Write Model

Activity is write-to-self only. It never synchronously iterates followers, group members, call participants, chat members, seller audiences, or notification recipients. One producer action causes at most one Activity row insert/update.

Repeatable user-facing history (`ad_view`, `ad_wishlist`, `user_search`, `user_follow`, `user_block`, `live_join`) coalesces. High-volume impressions, playback progress, taps, heartbeats and search keystrokes are not Activity events. No producer writes Notification rows.

## Pagination

`list_activity` uses keyset pagination ordered by:

```text
last_occurrence_at DESC,
creation DESC,
public_id DESC
```

Default page size is 20, maximum 50, and the query fetches only `limit + 1`. There is no lifetime `COUNT(*)` and no `OFFSET`.

The opaque versioned base64url cursor carries only the final sort tuple plus a hash of authenticated owner + group + type scope. Malformed/oversized cursors, owner reuse, or filter changes are rejected. `clear_activity` updates at most 500 rows and returns `has_more`.

## Caching / Realtime

Activity has no correctness cache, process-local cache, or realtime channel. MariaDB is the Activity authority and clients refresh through the API. Resource projections query authoritative domains in bounded batches. There are therefore no cross-user cache keys or invalidation races; a maximum 50-row private page plus batched authoritative visibility checks is preferred to privacy-sensitive cache duplication.

## Performance / Scalability

The hot write path is O(1) with one self-owned row and DB uniqueness; no popularity/follower fan-out exists. Read pages are capped at 50. Resource projection batches by resource class rather than querying once per Activity row. Timeline filters have matching composite indexes and retention uses an indexable `last_occurrence_at` range.

Retention is 180 days from latest occurrence. `aos.tasks.activity.cleanup_activity_retention` runs every five minutes and deletes at most ten batches of 5,000 rows (50,000 rows maximum per invocation; 600,000 rows/hour of catch-up capacity). Scheduler execution is maintenance only; duplicate runs are harmless and correctness does not depend on one node.

## Cross-feature Dependencies

```text
Authentication -> current authenticated owner only
Accounts       -> ACC-* identity, current account/display state, deletion lifecycle
Social         -> canonical bidirectional block/capability projection
Sellers        -> authoritative seller ownership/state consumed through Ads
Ads            -> public_id and current public/owner visibility
Media          -> authoritative media projection/URLs used by owning domains
Shorts         -> SHR-* identity and current visibility/moderation/audience policy
Live           -> LIVE-* identity, canonical account/block read access, message visibility
Reports        -> existing successful report path supplies hidden producer identity only

Calls / Chat / Reviews / Notifications / Saved Searches / Verification
              -> intentionally no Activity duplication in the current product
```

## Fresh-site schema

Current `AOS User Activity` DocType metadata and `install_activity_indexes` define the installed schema. `after_migrate` reasserts manual unique and query indexes after synchronization, without modifying existing feature data.

## Testing

`aos/api/activity/tests/` covers current architecture and behavior: taxonomy/typed metadata validation; public ID ownership; one-off and coalesced retry identities; DB uniqueness/concurrency boundaries; guest/unknown-field/IDOR rejection; keyset ordering/cursor owner/filter scope; block/account/resource fail-closed behavior; ended-Live behavior; bounded clear/retention; account purge; no public create/follower fan-out/Calls/Chat/Notification duplication; producer savepoint isolation; current index invariants; safe serialization/logging; documentation consolidation and cleanup contracts.

DB-backed tests use `FrappeTestCase` plus `AOSFeatureTestMixin.cleanup_feature_rows()` so Activity and any valid cross-feature fixtures are rolled back/removed. Repository/source validation can run without a site; the complete Frappe suite must still be run on the real staging site.
