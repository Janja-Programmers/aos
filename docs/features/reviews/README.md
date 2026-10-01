# Reviews

## Overview

Reviews is the ad-scoped marketplace feedback domain for AOS. An authenticated active account may create one Review for an eligible public Ad after canonical AOS communication with that Ad's Seller. Reviews use a required whole-star rating from 1–5, bounded title/comment text, up to five hardened `review_image` Media objects, explicit Like/Unlike/Dislike/Undislike reactions, reporting, automated moderation, and System Manager manual moderation in Frappe Desk. Only Approved Reviews contribute to public rating aggregates or appear on public Review surfaces.

The current product has no completed-order/booking proof connected to Reviews. `verified_interaction` therefore means server-verified AOS Conversation/Message interaction with the Seller, not a verified purchase.

## Responsibilities

Reviews owns Review content and lifecycle, one-Review-per-account-per-Ad enforcement, Review Media relationships, Review reaction state, Review-specific aggregate projections, public/private Review projections, Review pagination/filter/sort rules, moderation generation/state application, and Review reporting integration.

## Boundaries

Authentication owns session identity and active-account checks. Accounts owns public author identity and deleted-account projection. Ads owns canonical public Ad IDs and public Ad visibility. Sellers owns Seller identity/status. Media owns upload/storage/ownership/readiness/attachment/release and public attachment URLs. Verification owns verification state projected from canonical Profile data. Notifications owns creation/delivery infrastructure. Moderation owns generic automated classification/jobs/callback plumbing; Reviews owns the Review state transition receiving those decisions. Reports owns report reasons and the broader report lifecycle. Reviews does not duplicate any of those systems.

## Architecture

```text
AOS v1 Review wrapper
  -> auth + shared Redis-backed rate limit
  -> Review API error/transaction boundary
  -> ReviewService
     -> strict validation / eligibility
     -> hardened Ads / Accounts / Media / Seller contracts
     -> AOS Review + child Media rows / Reaction / Report
     -> Review lifecycle + aggregate hooks
     -> shared Moderation job/outbox
     -> canonical batched serializer
```

Writes use Frappe request transactions. Review API handling uses a savepoint only to roll back the failed Review operation; it does not commit. External/background moderation and notification delivery are not awaited inside Review database mutations.

## Data Model

`AOS Review` is the authoritative Review row. `AOS Review Image` is its child relationship to hardened Media. `AOS Review Reaction` stores one viewer relationship with a canonical `Like` or `Dislike` state. `AOS Review Report` stores one private report relationship per reporter/Review and consumes central `AOS Report Reason` records. Ad and Seller store derived Review count/rating projections plus an exact hidden integer rating sum for safe incremental updates.

## Fields

### AOS Review

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `public_id` | Data | server | Unique | Opaque immutable public Review identifier. |
| `ad` | Link → AOS Ad | yes | Composite indexes | Internal parent Ad relationship. |
| `reviewer` | Link → User | yes | Composite index | Server-derived Review author. |
| `review_key` | Data | server | Unique DB index | Durable one-review-per-reviewer/Ad key. |
| `rating` | Int | yes | Composite index | Canonical whole-star rating, 1–5 only. |
| `title` | Data | yes | no | Bounded plain-text title. |
| `comment` | Small Text | yes | no | Bounded plain-text Review body. |
| `review_images` | Table | no | child relationship | Ordered hardened Media attachments. |
| `status` | Select | server | Composite indexes | `Pending`, `Approved`, `Rejected`, `Hidden`, `Withdrawn`. |
| `like_count` | Int | server | helpful composite index | Derived Like projection. |
| `dislike_count` | Int | server | no | Derived Dislike projection. |
| `eligibility_basis` | Select | server | no | Current evidence class: `communication`. |
| `eligibility_reference` | Data | server | no | Private canonical communication evidence reference. |
| `moderation_generation` | Int | server | no | Prevents stale automated decisions from changing newer edits. |
| `edit_count` | Int | server | no | Number of accepted owner content edits. |
| `edited_on` | Datetime | server | no | Last owner content edit. |
| `withdrawn_on` | Datetime | server | no | Owner withdrawal timestamp. |
| `review_notes` | Small Text | server | no | Private bounded moderation/operator reason. |
| `reviewed_by` | Link → User | server | no | Last manual/automated operator identity where applicable. |
| `reviewed_on` | Datetime | server | no | Last Review decision timestamp. |

### AOS Review Image

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `media` | Link → AOS Media Object | yes | child parent ordering | Canonical Media attachment. URL is never duplicated into the child row. |

### AOS Review Reaction

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `review` | Link → AOS Review | yes | Unique pair (left prefix) | Internal Review relationship. |
| `user` | Link → User | yes | Search + unique pair | Session-derived reacting account. |
| `reaction` | Select | yes | no | Exactly `Like` or `Dislike`. |

Database uniqueness on `(review, user)` prevents simultaneous Like+Dislike rows for one viewer.

### AOS Review Report

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `review` | Link → AOS Review | yes | Search + report indexes | Report target. |
| `reported_by` | Link → User | yes | Search + unique pair | Session-derived reporter. |
| `reason` | Link → AOS Report Reason | yes | no | Central enabled report reason classified for the internal Review scope. |
| `details` | Small Text | no | no | Bounded report detail. |
| `status` | Select | yes | Search | Reports lifecycle state. |
| `reviewed_by` | Link → User | server | no | Report operator. |
| `reviewed_on` | Datetime | server | no | Report review timestamp. |

### Derived Ad/Seller fields

`AOS Ad.review_rating_sum` and `AOS Seller.review_rating_sum` are hidden exact integer sums for Approved Review ratings. They combine with existing `total_reviews` and average fields to support constant-size transactional deltas without lossy average arithmetic or full rescans on normal writes.

## Relationships

```text
User/Account --authors--> AOS Review --belongs to--> AOS Ad --belongs to--> AOS Seller
                               |
                               +--children--> AOS Review Image --references--> AOS Media Object
                               |
                               +--1 per viewer--> AOS Review Reaction
                               |
                               +--reported by--> AOS Review Report --reason--> AOS Report Reason
                               |
                               +--classified through--> shared Moderation Job/outbox
```

All public client references use Ad/Review/Seller/Account public identifiers where those hardened domains define them. Frappe document names remain internal Links.

## Naming Strategy

`AOS Review` uses `new_prefixed_name("REVIEW")` for its internal Frappe name and a separate collision-resistant `review_…` public ID from the hardened public-ID helper. `AOS Review Reaction` uses a deterministic hash name derived from `(review, user)` plus a database unique index on that pair; no sequence or process-local counter is used. `AOS Review Report` uses `new_prefixed_name("RREPORT")`. Child `AOS Review Image` naming remains framework child-row identity and is never public. No Review high-write DocType uses Frappe naming series.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `create_review` | POST | Session required | Client |
| `delete_review` | POST | Session required | Client |
| `dislike_review` | POST | Session required | Client |
| `get_review` | GET | Guest allowed | Client |
| `get_review_viewer_state` | GET | Guest allowed | Client |
| `like_review` | POST | Session required | Client |
| `list_my_reviews` | GET | Session required | Client |
| `list_reviews` | GET | Guest allowed | Client |
| `list_reviews_received` | GET | Session required | Client |
| `report_review` | POST | Session required | Client |
| `undislike_review` | POST | Session required | Client |
| `unlike_review` | POST | Session required | Client |
| `update_review` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

The canonical public contract is under `aos.api.v1.reviews`; Frappe transport-owned arguments are stripped before domain validation. Unknown domain fields are rejected.

| Method | Purpose | Auth | Inputs | Idempotency / important errors | Rate limit / minute |
|---|---|---|---|---|---:|
| `create_review` | Create Pending Review | session | `ad_id`, `rating`, `title`, `comment`, optional canonical Media ID `media[]` | DB unique one-per-account/Ad; duplicate → `REVIEW_ALREADY_EXISTS` | 6 |
| `update_review` | Owner edit and resubmit | session | `review_id`, `version`, optional changed content/media | Optimistic version; unchanged is `changed=false`; approved edits return Pending | 12 |
| `delete_review` | Owner withdrawal | session | `review_id`, `version` | Repeated withdrawal succeeds with `changed=false` | 8 |
| `get_review` | Canonical Review detail | guest/session | `review_id` | Non-owner sees Approved + publicly eligible parent only | 120 |
| `list_reviews` | Public Ad Reviews | guest/session | `ad_id`, optional `sort`, `rating`, `with_media`, `limit`, `cursor` | Keyset cursor is opaque/validated/query-bound | 120 |
| `list_my_reviews` | Owner Review history | session | optional `sort`, `status`, `rating`, `with_media`, `limit`, `cursor` | Includes owner-private lifecycle fields | 90 |
| `list_reviews_received` | Seller's Approved Reviews | session seller | optional `sort`, `rating`, `with_media`, `limit`, `cursor` | Seller identity comes from session | 90 |
| `get_review_viewer_state` | Eligibility + existing Review state | guest/session | `ad_id` | Read-only | 120 |
| `like_review` | Set viewer state to Like | session | `review_id` | Repeated Like `changed=false`; Dislike→Like atomic | 60 |
| `unlike_review` | Remove Like only | session | `review_id` | Does not remove Dislike | 60 |
| `dislike_review` | Set viewer state to Dislike | session | `review_id` | Repeated Dislike `changed=false`; Like→Dislike atomic | 60 |
| `undislike_review` | Remove Dislike only | session | `review_id` | Does not remove Like | 60 |
| `report_review` | Report public Review | session | `review_id`, `reason`, optional `details` | One report per reporter/Review; retry `changed=false` | 8 |

Public sorts are `newest`, `helpful`, `rating_high`, and `rating_low`. Owner history also supports `oldest`. Public filters are exact rating and `with_media`. Ordering always includes deterministic `creation`/`public_id` tie-breakers. Cursor context binds scope, sort, and filters; malformed or incompatible cursors raise `INVALID_REVIEW_CURSOR`.

## Review Lifecycle

New Reviews always enter `Pending`. Automated moderation may apply `Pending → Approved`, `Pending → Rejected`, or leave `Pending` for manual review. An owner content/media edit from `Pending`, `Approved`, or `Rejected` enters `Pending`, increments `moderation_generation`, and queues current content for moderation. Owner deletion is a durable withdrawal: `Pending|Approved|Rejected → Withdrawn`. Manual Desk actions may approve `Pending|Rejected|Hidden` and reject `Pending|Approved|Hidden`. Only Approved contributes to aggregates and public projection. Hidden and Withdrawn are never public.

Automated decisions carry the captured `moderation_generation`; stale callbacks are no-ops. Any automated decision arriving after a human decision sees non-Pending state and is also a no-op.

## Desk Moderation

Manual Review moderation is performed on `AOS Review` in Frappe Desk, not the customer web application. The DocType list exposes Review ID, Ad, Reviewer, Rating, Status, and standard creation metadata; Status/Ad/Reviewer are standard filters. The Review form provides **Approve Review** and **Reject Review** actions appropriate to the current status. Reject requires a reason.

Only the existing `System Manager` DocType role has Review write permission. Desk create/delete/share are disabled so ordinary operator work cannot bypass the Review lifecycle; moderation is performed through the explicit actions. The Desk method `aos.aos.doctype.aos_review.review.review_review` checks current write permission again, requires the public Review ID plus optimistic `version`, and delegates to the Review-owned transition service. Status/content/ownership fields are read-only in the DocType and public Review APIs never accept moderation/owner/counter fields. `AOS Review Reaction` is read-only to Desk operators and its reacting user is always overwritten from the authenticated session on insert. All transitions are revalidated server-side.

## Reactions

One `AOS Review Reaction` row is authoritative for `(review,user)`. `like_review` creates Like or atomically changes Dislike→Like. `dislike_review` creates Dislike or atomically changes Like→Dislike. `unlike_review` removes only Like; if current state is Dislike it returns unchanged. `undislike_review` removes only Dislike; if current state is Like it returns unchanged. Duplicate/retried intent returns success with `changed=false`. The unique DB index is authoritative under races; relationship hooks update non-negative derived counters atomically.

## Media

Clients submit only hardened Media IDs in `media`. Reviews calls `MediaService.validate_media_for_use(... purpose="review_image")`, then `attach_media`/`release_media`; it never uploads files or duplicates URLs/storage state. The child table stores only the Media Link and preserves order. Public serialization batch-loads attachment URLs through `get_public_attachment_url_map`. Withdrawal detaches current Review Media. Edit attaches additions and releases removals. Media ownership, status, purpose, attachment count, storage, deletion/orphan handling, and other references remain Media responsibilities.

## Aggregates

Approved Review rows are durable truth. On a lifecycle/rating transition, the Review controller computes old/new `(rating_sum,count)` contribution and locks Ad then Seller in a fixed order. It updates exact integer sums/counts and derives the average. This avoids race-prone read-modify-write and full Review scans on normal writes. Reconciliation functions recompute Ad/Seller aggregates from Approved source rows and reaction counts from relationship rows for drift repair. Rating distribution is calculated from Approved rows for the requested Ad.

## Cross-feature Dependencies

- **Reviews → Authentication:** session identity; clients never choose reviewer/reactor/reporter.
- **Reviews → Accounts:** canonical public author display/anonymized identity and active account policy.
- **Reviews → Ads:** canonical public Ad resolution and public visibility; internal Link retained only in storage.
- **Reviews → Sellers:** canonical Seller relationship/identity and seller-owned received Reviews.
- **Reviews → Media:** `review_image` validation, ownership, attachment/release, public URL projection.
- **Reviews → Verification:** batched Profile verification display state only.
- **Reviews → Notifications:** approval/rejection and initial seller `review_received` notification via hardened service; notification failures do not roll back a valid moderation decision.
- **Reviews → Moderation:** generic job/provider/outbox infrastructure; Reviews owns generation-safe lifecycle application and Desk override.
- **Reviews → Social:** canonical block relationship suppresses inaccessible author content/reactions.
- **Reviews → Reports:** central active reasons and existing report operations.

## Transaction / Concurrency Model

One Review per account/Ad is enforced by unique `review_key`; create catches duplicate-key races and reports the existing public Review. Reactions use a unique `(review,user)` invariant and row locks for state transitions. Review update/withdraw/manual moderation lock the Review row and require optimistic `modified` version where a client/operator is changing mutable state. Aggregate targets lock in deterministic Ad→Seller order. Media is validated/attached through its own shared locking contract. No Review path uses `frappe.db.commit()`; request/job transaction ownership remains with Frappe. Correctness does not depend on Python globals, sticky sessions, worker-local locks, or worker-local counters.

## Caching

Reviews keeps no mutable correctness cache. Rate limiting uses the hardened shared rate-limit infrastructure, and other shared dependencies may cache their own immutable/projection data. Database state and durable constraints remain authoritative across application nodes.

## Performance / Scalability

Public Review lists use bounded keyset pagination and query-specific composite indexes; no offset scan or total-count query is performed. Common author, Ad public ID, Verification, Media URL, and viewer-reaction data is batch loaded, avoiding per-Review dependency queries. High-write names are non-sequential. Normal aggregate writes are constant-size deltas rather than scans.

| Operation | DB queries/work | Main index | Write contention | Multi-node safe | Main concern |
|---|---|---|---|---|---|
| Create Review | eligibility reads, Media validation, one Review insert + child/attachments, moderation enqueue | `uq_aos_review_key` | same reviewer/Ad; Media target lock | yes | moderation/Media throughput |
| Update Review | Review row lock, changed fields/media, aggregate delta if previously public, moderation enqueue | Review public ID + row PK | one Review row + changed Media | yes | rapid repeated edits |
| Delete Review | Review row lock, lifecycle update, aggregate delta, Media release | Review public ID + row PK | one Review + aggregate targets | yes | large Media release fanout is capped at 5 |
| List Reviews | one indexed page query + bounded distribution + batched projections | `idx_aos_review_public_*` | none | yes | very hot Ads may need cache/read-replica strategy after measurement |
| Like/Unlike | public visibility read, one relationship row lock/insert/delete, atomic counter delta | `uq_aos_review_reaction_user` | popular Review counter row | yes | hot Review reaction contention |
| Dislike/Undislike | same as Like | `uq_aos_review_reaction_user` | popular Review counter row | yes | hot Review reaction contention |
| Approval/Rejection | Review row lock + small Ad/Seller aggregate delta + after-transaction delivery infrastructure | `idx_aos_review_moderation` | Review + Ad/Seller aggregate rows | yes | hot seller aggregate row |

Application structure is designed for horizontal workers and approximately one million global users, but infrastructure capacity/concurrent throughput must be established by production-like load testing, database sizing, queue observation, and failure testing; no request-per-second capacity is claimed here.

## Testing

Fast tests cover strict validation, cursor/query binding, eligibility, current API/schema contracts, naming/index rules, cleanup rules, moderation wiring, and rate-limit registration. `test_reviews_database.py` uses shared hardened fixtures to create valid Accounts, Sellers, Categories, Locations, Ads, Conversations and Media, then verifies create uniqueness/public IDs, owner/version authorization, withdrawal and aggregates, Media ownership/lifecycle, manual/automated moderation precedence, session-derived reaction ownership, reaction idempotency/switching/counters, keyset pagination/public visibility, and current-schema indexes. `test_reviews_concurrency.py` exercises duplicate-create and competing reaction race branches so database winners remain authoritative. `AOSFeatureTestMixin.cleanup_feature_rows()` removes reaction/report/image rows before Reviews/Ads, tracked Media and dependent feature records, then users/profiles; teardown is invoked by `FrappeTestCase` even after assertion failures.

Server acceptance remains:

```bash
bench --site "$SITE" migrate
bench --site "$SITE" run-tests --app aos
```

## Security
Review creation, reaction and reporting require server-derived actor identity, owning Ads visibility and Social block/privacy checks. Media attachments are canonical Media IDs; automatic and human Moderation decisions are authoritative, and the public serializer must not leak Frappe identities, reviewer evidence or administrative fields.
