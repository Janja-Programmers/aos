# Social

## Overview
Social owns the AOS social graph for follow/unfollow and user blocking, including authoritative relationship state, profile counters, realtime/notification side effects, privacy enforcement and account-lifecycle cleanup.

## Responsibilities
Social owns follow/block relationship persistence, allowed transitions, relationship queries, counter consistency, block-aware policy and social mutation APIs.

## Boundaries
Accounts owns account/profile identity, Notifications owns notification records/delivery, and Ads/Sellers consume social/block policy through Social services. Social does not recreate those domains' serializers or storage.

## Architecture
```text
Versioned Social API -> social service/policy/repository -> AOS Follow / AOS User Block -> Accounts counter projection
                                          -> NotificationService / realtime
```

## Data Model
- `AOS Follow`: follower/following relationship with database uniqueness and active-state semantics; hash-named.
- `AOS User Block`: directional block relationship with database uniqueness and lifecycle state.
- `AOS Profile` counters: Accounts-owned read projections maintained from Social mutations/reconciliation.

## Fields
| Model | Field | Required / constraint | Purpose |
|---|---|---|---|
| AOS Follow | follower / following | unique pair | Canonical social edge. |
| AOS Follow | status/timestamps | transition constrained | Follow lifecycle. |
| AOS User Block | blocker / blocked | unique pair | Canonical privacy edge. |
| AOS User Block | status/timestamps | transition constrained | Block lifecycle. |
| AOS Profile | follower/following counts | maintained projection | Fast public counts. |

## API
Social endpoints expose follow/unfollow, relationship state, followers/following lists and block/unblock behavior with authenticated identity derived from the session. Pagination is bounded and clients cannot supply another user's ownership identity for mutations.

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `block_user` | POST | Session required | Client |
| `follow` | POST | Session required | Client |
| `get_block_status` | GET | Session required | Client |
| `get_followers` | GET | Session required | Client |
| `get_following` | GET | Session required | Client |
| `get_friends` | GET | Session required | Client |
| `get_relationship_status` | GET | Session required | Client |
| `list_blocked_users` | GET | Session required | Client |
| `search_users` | GET | Session required | Client |
| `unblock_user` | POST | Session required | Client |
| `unfollow` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Cross-feature Dependencies
Social resolves users through Accounts and emits notifications through Notifications. Ads/Sellers use Social's block/relationship boundaries where privacy/visibility requires them.

## Transaction / Concurrency Model
Pair uniqueness and database row locking/atomic updates serialize competing relationship mutations. Counter deltas are applied only when relationship state actually changes, and reconciliation facilities protect against derived-count drift. No process-local lock is authoritative.

## Caching
Relationship correctness is database-backed; any cached/profile projection state is invalidated or reconciled from canonical rows. Realtime events are not the source of truth.

## Performance / Scalability
Composite pair/list indexes support mutation checks and bounded follower/following pages. Mutations avoid duplicate edge rows under races through DB uniqueness. High-fanout graph workloads and realtime throughput require representative load tests.

## Testing
Tests under `aos/api/social/tests` and shared suites cover transitions, duplicates/races, blocks, counts, notifications, pagination, privacy and account lifecycle. Fixture cleanup removes social edges before users/profiles so later suites do not inherit relationship counts.

## Detailed Reference

### Feature overview

Social owns the user-to-user relationship graph for following, mutual-follow friendship, and directional blocking. It owns the rules and capability projection derived from that graph; it does **not** own authentication, account/profile identity, notification delivery, verification state, media, location/localization reference data, or catalog data.

The production model intentionally has no friend-request workflow. A friendship is the deterministic projection of two reciprocal follow edges. Public Social identity is always the canonical Accounts `ACC-*` account ID. Internal Frappe `User.name` values are persistence details and are never accepted or returned by Social client APIs.

The public contract uses explicit follow/unfollow mutations, opaque account IDs, and cursor pagination.

### Dependencies

- **Authentication** supplies the canonical authenticated session actor. Social never accepts an actor identity from client input.
- **Accounts** owns `ACC-*` identity, display name, profile image reference, account lifecycle, and the profile counters that Social updates transactionally.
- **Notifications** owns persistent notifications and durable delivery. Social creates only the canonical follow notification through `NotificationService`.
- **Verification** remains authoritative for verification state. Social list serializers may project the Accounts/Verification-owned `is_verified` value; Social never stores verification lifecycle state.
- **Media** remains authoritative for avatar media and public URLs. Social does not store or manipulate media objects.
- **Localization** is not owned or duplicated by Social.
- **Catalog** is not part of the current Social relationship model.

### Relationship model

#### Follow

`AOS Follow` is a directed edge from `follower_user` to `following_user`. Exactly one edge may exist per ordered pair. Follow and unfollow are explicit, retry-safe set operations; there is no toggle operation.

A user cannot follow themselves, a missing/unavailable account, or an account blocked in either direction. Unfollow is allowed against an unavailable target so a retained graph can still be cleaned safely. Pair mutations lock the two underlying User rows in deterministic order; the unique follow-pair index remains the database backstop against duplicate races.

#### Friendship

Friendship is **derived**, not independently stored. Accounts A and B are friends exactly when both `A -> B` and `B -> A` follow edges exist. There are no friend requests, pending/accepted/rejected friend-request states, or separate friendship rows.

`total_friends` is a denormalized count on `AOS Profile`, maintained when the second reciprocal edge appears or either reciprocal edge disappears. A bounded reconciliation path can recompute all three Social counters from the graph.

#### Blocking

`AOS User Block` is one directional row per `(blocker_user, blocked_user)` pair with state `Active` or `Unblocked`. Re-blocking reuses that row. Blocking is idempotent and has higher precedence than follow/friendship: activating a block removes follow edges in both directions in the same caller-owned transaction and updates counters atomically. Unblocking never restores old follows.

An active block in either direction disables profile visibility and Social interaction capabilities. The public viewer is not told that another user blocked them: direct relationship/block-status reads return `SOCIAL_PROFILE_UNAVAILABLE` when the only active block is incoming. A viewer can still observe their own outgoing block so the UI can offer Unblock.

### State machines

#### Follow edge

- absent -> present: `follow`
- present -> present: retry/idempotent `follow`
- present -> absent: `unfollow`, or either-direction `block`
- absent -> absent: retry/idempotent `unfollow`

#### Directional block

- absent/`Unblocked` -> `Active`: `block`
- `Active` -> `Active`: retry/idempotent `block`; a changed reason may update metadata without creating a second row
- `Active` -> `Unblocked`: `unblock`
- absent/`Unblocked` -> `Unblocked`: retry/idempotent `unblock`

Block transitions never create follow edges.

### Data layer

#### `AOS Follow`

Purpose: authoritative directed follow edge.

Key fields:

- `follower_user`: internal Accounts/Frappe user reference, server controlled
- `following_user`: internal Accounts/Frappe user reference, server controlled
- standard `creation` and `name`: deterministic keyset ordering

Invariants and indexes:

- unique `(follower_user, following_user)` via `uq_social_follow_pair`
- following-page index `(follower_user, creation, name)`
- follower-page index `(following_user, creation, name)`
- reverse-edge lookup index `(following_user, follower_user)`
- self-follow is rejected server-side

Retention: edges are retained while Accounts is recoverably deleted so restore is O(1). They are removed during permanent Accounts purge.

#### `AOS User Block`

Purpose: authoritative directional block state.

Key fields:

- `blocker_user`, `blocked_user`: internal account references, server controlled
- `status`: `Active` or `Unblocked`
- `reason`: bounded optional private reason
- `blocked_at`, `unblocked_at`: server timestamps

Invariants and indexes:

- unique `(blocker_user, blocked_user)` via `uq_social_block_pair`
- owner block-list index `(blocker_user, status, blocked_at, name)`
- reverse block lookup index `(blocked_user, status, blocker_user)`
- self-block is rejected

Retention: rows are retained through the recoverable-delete window and are physically removed during permanent account purge.

#### `AOS Profile` Social counters

Accounts owns the profile row; Social transactionally maintains:

- `total_followers`
- `total_following`
- `total_friends`

Client-supplied counter values are never accepted. Atomic SQL deltas use a zero floor, so counters cannot become negative. The `SocialRepository.sync_counters()` reconciliation path recomputes the three values from indexed graph data and is reserved for repair/purge reconciliation rather than normal profile reads.

`idx_social_profile_discovery(account_status, display_name, name)` supports bounded account discovery. Search uses Accounts-owned display name or canonical `ACC-*` prefix only; email/Frappe User identity is not searchable through Social.

### Counts consistency model

Normal follow, unfollow, and block mutations update the graph and the relevant Accounts profile counters in the same database transaction. If the transaction rolls back, both graph and counters roll back. This provides strong transactional consistency for normal Social writes.

During recoverable account deletion, retained graph edges and counters are intentionally preserved for restoration while public visibility is suppressed by Accounts lifecycle policy. Permanent purge removes graph rows in bounded batches and invokes counter reconciliation for affected accounts.

### Concurrency and transactional correctness

All pair mutations acquire the two User-row locks in deterministic lexical order before re-checking lifecycle and relationship state. This serializes follow/follow, follow/unfollow, cross-follow friendship creation, follow/block, unblock/follow, and block/unblock races without graph-wide locks. Composite uniqueness remains the final race guard.

Social API implementations use operation-level savepoints and leave final commit/rollback ownership to Frappe's request transaction. Relationship state never depends on realtime delivery.

### Pagination and high-traffic reads

All relationship lists, block lists, and user search are bounded (`1..50`, default `20`). They use signed, versioned keyset cursors; requests outside the documented cursor contract are rejected.

- followers/following: descending `(creation, name)` cursor
- friends: descending `(outgoing follow creation, outgoing edge name)` cursor
- block list: descending `(blocked_at, block row name)` cursor
- account search: ascending `(display_name, account_id)` cursor bound to a fingerprint of the search query

List responses contain `items`, `limit`, `has_more`, and `next_cursor`; list filters additionally echo `search`, while discovery echoes `query`. No full `COUNT(*)` is performed per list request. Identity/relationship serialization is batched to avoid N+1 lookups.

### Public API

Every route is authenticated and takes the actor from the canonical Authentication session. Unknown fields are rejected. GET routes use query parameters only; POST routes use request fields. All target mutations/reads accept exactly `account_id` for the target and require canonical `ACC-*` syntax.



#### `follow` — POST

Frontend use: follow an account from profile/discovery/list surfaces.

Request: `account_id`.

Response data: `status=followed`, `changed`, the public viewer-relative relationship projection, `target_total_followers`, `current_total_following`, and their display forms. A successful response never exposes an incoming block direction.

Idempotency: set semantics. Repeating a successful follow returns `changed=false`; uniqueness and pair locking prevent duplicate edges/counter increments. A viewer-owned block returns `SOCIAL_BLOCKED`; an incoming-only block is represented as `SOCIAL_PROFILE_UNAVAILABLE`.

Errors: shared auth/rate errors plus `SOCIAL_UNKNOWN_FIELD`, `SOCIAL_TARGET_REQUIRED`, `SOCIAL_INVALID_ACCOUNT_ID`, `SOCIAL_ACCOUNT_NOT_FOUND`, `SOCIAL_SELF_ACTION`, `SOCIAL_ACTOR_UNAVAILABLE`, `SOCIAL_PROFILE_UNAVAILABLE`, `SOCIAL_BLOCKED`, and `SOCIAL_INTERNAL_ERROR`.

Application rate: 24 requests/account/minute.

#### `unfollow` — POST

Frontend use: remove the authenticated account's outgoing follow edge.

Request: `account_id`.

Response data: `status=unfollowed`, `changed`, a privacy-safe relationship projection, `target_total_followers`, `current_total_following`, and display forms.

Idempotency: set semantics. Repeating unfollow returns `changed=false`. It is valid against an inactive/deleted target so retained relationships can be cleaned. Incoming block direction remains masked in the response.

Errors: shared auth/rate errors plus request/identity/self/actor failures and `SOCIAL_INTERNAL_ERROR`.

Application rate: 36 requests/account/minute.

#### `get_relationship_status` — GET

Frontend use: obtain viewer-relative relationship/capability state for an active target.

Request query: `account_id`.

Response data: `account_id`, `is_self`, `is_following`, `is_followed_by`, `is_friend`, `relationship_status`, `action_label`, `is_blocked_by_me`, `has_blocked_me`, `is_blocked`, `block_status`, `can_follow`, `can_message`, `can_call`, and `can_view_profile`. `has_blocked_me` is never returned as `true` by this public API. Incoming-only block state is privacy-protected as `SOCIAL_PROFILE_UNAVAILABLE`; for a mutual block, only the viewer-owned direction is exposed.

Idempotency: read-only.

Errors: shared auth/rate errors plus request/identity/self/availability/cursor-independent validation errors and `SOCIAL_INTERNAL_ERROR`.

Application rate: 180 requests/account/minute.

#### `get_following`, `get_followers`, `get_friends` — GET

Frontend use: bounded relationship-list screens.

Request query: optional `limit` (`1..50`, default `20`), `cursor`, and optional `search` (2..80 character Accounts display-name or `ACC-*` prefix).

Response data: `items`, `limit`, normalized `search`, `has_more`, and `next_cursor`. Each item contains Accounts-owned public identity/display/avatar/verification projections, bounded counter projections, follow timestamps where applicable, and the privacy-safe relationship projection. Internal User identities are never included.

Pagination: signed keyset cursor only. A cursor is bound to the endpoint kind and normalized search fingerprint; changing the filter or tampering with the cursor returns `SOCIAL_INVALID_CURSOR`. Empty terminal pages return `has_more=false` and `next_cursor=null`.

Idempotency: read-only.

Errors: shared auth/rate errors plus `SOCIAL_UNKNOWN_FIELD`, `SOCIAL_INVALID_SEARCH`, `SOCIAL_INVALID_LIMIT`, `SOCIAL_INVALID_CURSOR`, `SOCIAL_ACTOR_UNAVAILABLE`, and `SOCIAL_INTERNAL_ERROR`.

Application rate: 90 requests/account/minute per list surface.

#### `search_users` — GET

Frontend use: bounded Social account discovery.

Request query: `query` (2..80 characters), optional `limit` (`1..50`, default `20`), optional `cursor`.

Response data: `items`, `limit`, normalized `query`, `has_more`, and `next_cursor`. Results are active discoverable Accounts matched by display-name or `ACC-*` prefix; the viewer and any account blocked in either direction are excluded. Email/internal User identity is never searched or returned.

Pagination: ascending `(display_name, account_id)` signed keyset cursor bound to the normalized query fingerprint.

Idempotency: read-only.

Errors: shared auth/rate errors plus `SOCIAL_UNKNOWN_FIELD`, `SOCIAL_INVALID_SEARCH`, `SOCIAL_INVALID_LIMIT`, `SOCIAL_INVALID_CURSOR`, `SOCIAL_ACTOR_UNAVAILABLE`, and `SOCIAL_INTERNAL_ERROR`.

Application rate: 40 requests/account/minute.

#### `block_user` — POST

Frontend use: block an account from profile/safety surfaces.

Request: `account_id`, optional normalized private `reason` up to 300 characters.

Response data: opaque block `id`, `status=blocked`, `changed`, and a privacy-safe relationship projection. The target's incoming block direction is never exposed, including for mutual blocks.

Semantics/idempotency: idempotently activates the viewer's directional block and removes both follow directions in the same transaction. Repeating the action reuses the unique block row and returns `changed=false` unless relationship cleanup or block state actually changed. A changed private reason may update metadata without creating another block row.

Errors: shared auth/rate errors plus request/identity/self/availability/reason validation errors and `SOCIAL_INTERNAL_ERROR`.

Application rate: 12 requests/account/minute.

#### `unblock_user` — POST

Frontend use: remove the viewer-owned block.

Request: `account_id`.

Response data: `status=unblocked`, `changed`, and a privacy-safe relationship projection. If the target independently blocks the viewer, the mutation still succeeds but the response remains generically unavailable and never identifies the incoming block.

Semantics/idempotency: idempotently deactivates only the viewer's directional block. Prior follows are never restored. Repeating the action returns `changed=false`.

Errors: shared auth/rate errors plus request/identity/self/actor failures and `SOCIAL_INTERNAL_ERROR`.

Application rate: 18 requests/account/minute.

#### `get_block_status` — GET

Frontend use: determine the viewer-owned block state/capabilities needed to render block/unblock controls.

Request query: `account_id`.

Response data: `account_id`, `is_blocked_by_me`, `has_blocked_me`, `is_blocked`, `block_status`, `can_follow`, `can_message`, `can_call`, and `can_view_profile`. `has_blocked_me` is never returned as `true`; incoming-only state is represented as `SOCIAL_PROFILE_UNAVAILABLE`, and mutual state exposes only the viewer-owned direction.

Idempotency: read-only.

Errors: shared auth/rate errors plus request/identity/self/availability errors and `SOCIAL_INTERNAL_ERROR`.

Application rate: 120 requests/account/minute.

#### `list_blocked_users` — GET

Frontend use: owner-only block-management screen.

Request query: optional `limit` (`1..50`, default `20`) and `cursor`.

Response data: `items`, `limit`, `has_more`, and `next_cursor`. Each item contains an opaque block ID, canonical public account ID, safe display/avatar state, private owner-visible reason, `blocked_at`, and `status=blocked`. Deleted/unavailable targets are represented without exposing stale profile PII.

Pagination: descending `(blocked_at, block row name)` signed keyset cursor. There is no endpoint for listing accounts that blocked the actor.

Idempotency: read-only.

Errors: shared auth/rate errors plus `SOCIAL_UNKNOWN_FIELD`, `SOCIAL_INVALID_LIMIT`, `SOCIAL_INVALID_CURSOR`, `SOCIAL_ACTOR_UNAVAILABLE`, and `SOCIAL_INTERNAL_ERROR`.

Application rate: 90 requests/account/minute.

#### Stable errors

Social uses the shared response envelope and maps expected domain failures to stable codes, including:

- `SOCIAL_UNKNOWN_FIELD`
- `SOCIAL_TARGET_REQUIRED`
- `SOCIAL_INVALID_ACCOUNT_ID`
- `SOCIAL_ACCOUNT_NOT_FOUND`
- `SOCIAL_SELF_ACTION`
- `SOCIAL_ACTOR_UNAVAILABLE`
- `SOCIAL_PROFILE_UNAVAILABLE`
- `SOCIAL_BLOCKED`
- `SOCIAL_INVALID_REASON`
- `SOCIAL_INVALID_SEARCH`
- `SOCIAL_INVALID_LIMIT`
- `SOCIAL_INVALID_CURSOR`
- shared `RATE_LIMIT`
- `SOCIAL_INTERNAL_ERROR` for unexpected failures

Raw database/Frappe exceptions are never part of the public response.

### Accounts integration

`SocialCapabilityService` is the canonical internal read boundary for viewer-target relationship and capabilities. Accounts consumes `relationship_projection()` instead of querying Social DocTypes or importing a public API helper. It preserves the established Accounts profile response fields, including `is_self`, follow/friend status, block-derived capabilities, and `friends_count`.

Accounts continues to own display name, avatar/media link, verification projection, account status, and public identity. Social list serialization batch-loads those Accounts-owned identity fields; it does not duplicate them.

### Notifications

Only a successful transition from not-following to following generates a Social notification. The recipient is the followed account and the actor is the follower. Social calls the canonical Notifications service with canonical type/event semantics and a stable per-follow dedupe key; a short retry-suppression window prevents notification spam after rapid unfollow/refollow cycles.

The Notifications service persists the notification and durable delivery/outbox work in the same database transaction; external delivery/realtime work is performed by the finalized Notifications architecture after commit. Notification creation/delivery failure is isolated from the Social graph and never rolls back an otherwise valid follow mutation.

No notification is generated for unfollow, block, unblock, or derived friendship. In particular, blocking never sends a notification to the blocked account because that would leak security-sensitive state. The current product has no friend-request/accept event.

### Realtime events

Social has no separate client-authoritative relationship realtime channel. Finalized Notifications may deliver notification realtime events. Later features must recover relationship truth from normal Social/Accounts APIs rather than relying on delivery of a transient event.

### Account lifecycle

Accounts remains authoritative for deletion, restoration, and permanent purge.

- **Recoverable deletion:** Social graph/block rows are retained; Accounts lifecycle makes the profile unavailable and normal Social discovery excludes it. No Social-owned delete API exists.
- **Restore:** retained rows become usable again under the same uniqueness rules; no duplicate follow/block records are recreated.
- **Permanent purge:** account purge deletes follow edges and all block rows involving the account in bounded batches, then reconciles counters for affected surviving accounts.

Notifications and discovery both respect account availability so deleted accounts are not treated as active recipients/targets.

### Security and privacy

- public identity is `ACC-*` only
- authenticated session identity is always the actor; client input cannot act for another account
- block lists are owner-only
- incoming block direction is never returned by public Social APIs; incoming-only state is represented as generic profile unavailability
- blocked accounts are excluded from relationship lists and search in either direction
- display/search data comes from Accounts, not internal User email/name
- block reason is bounded and owner-private
- no raw Social DocType mutation endpoint exists
- signed cursors prevent client-crafted deep traversal state
- endpoint-specific application limits supplement the shared production edge limiter

### Internal services

#### `SocialCapabilityService`

Supported internal decisions:

- `relationship_projection(viewer, target)`
- `projection_map(viewer, targets)`
- `is_blocked(viewer, target)`
- `can_view_profile(viewer, target)`
- `can_follow(viewer, target)`
- `can_message(viewer, target)`
- `can_call(viewer, target)`

These are backend contracts for Accounts and later Chat/Call consumers. They are not client/Postman APIs. Social defines the capability decision only; it does not harden or implement Chat/Call transport in this phase.

`SocialRepository.list_active_followers_for_event_page()` is a bounded internal fan-out primitive used by established backend event/notification workflows. It is not a public endpoint and never establishes source-of-truth delivery semantics.

### Observability

Public operations emit privacy-safe structured Social logs with an allowlisted operation/outcome/reason vocabulary, bounded latency/count fields, and no internal user identities, emails, block reasons, request payloads, or raw exception text.

Operational signals should distinguish validation/rejection, rate limiting, idempotent retries, notification suppression/failure, and unexpected internal failure. Database uniqueness and counter reconciliation provide deterministic recovery boundaries.

### Fresh-site schema and deployment

`aos.patches.v1_0.install_social_indexes` is schema-only and idempotent. `aos.migrate.after_migrate` reasserts the same manual composite indexes after DocType synchronization.

A fresh `bench migrate` creates the DocTypes/fields and installs all Social uniqueness/query indexes.

### Validation and regression coverage

The repository gates cover the single Social README, public endpoint inventory, rate-limit registry, source architecture, strict request fields, canonical IDs, the explicit follow/unfollow contract, schema/index declarations, cursor pagination, counter strategy, Accounts capability integration, Notifications integration, and permanent purge behavior. Database-backed concurrency contracts additionally verify deterministic pair locks, duplicate-insert winner semantics, reciprocal-friend counter transitions, and mutual-follow cleanup. Staging remains authoritative for real database lock/race behavior, schema installation, notification outbox behavior, account lifecycle, and end-to-end API behavior.
