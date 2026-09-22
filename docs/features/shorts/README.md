# Shorts + Video Processing

## Overview

AOS Shorts is the client-facing short-form content domain. Video Processing is its private infrastructure companion. Shorts supports immersive Video and Photo posts, exactly two primary feeds (`For You` and `Following`), and four server-derived Content Modes: `shop`, `geo`, `vibes`, and `learn`.

## Responsibilities

Shorts owns drafts, publication lifecycle, post permissions, captions, ordered Photo assets, hashtags, canonical account mentions, canonical Ad links, Sounds, comments, likes, saves, reposts, Not Interested feedback, watch/recommendation signals, creator management, reuse relationships, and client feed/list projections. Video Processing owns durable technical jobs, normalized playback assets, adaptive HLS, posters, storyboards, original-audio extraction, cached watermarked downloads, and derived side-by-side/segment compositions.

## Boundaries

Raw and derived objects are owned by hardened Media. Creator identity comes from Accounts and Verification. Follow/block/relationship policy comes from Social. Linked Shop Ads point to canonical Ads, but Content Modes themselves are semantic classifier output. Geo means geography-related content such as mountains, travel, landmarks, streets, maps, scenery, and places; it has no Localization/Maps/location relationship. Notifications use the Notifications service. Search Ranking receives projection events but does not own Shorts lifecycle. Reports and Moderation are consumed through their existing integration boundaries; their domains are not redesigned here. Live, Calls, Chat, and Activity remain separate domains. Video Processing callbacks and Desk review actions are internal and are not client APIs.

## Architecture

Client requests enter `aos.api.v1.shorts` and delegate to `aos.services.shorts` through strict endpoint allowlists and request savepoints. Durable processing jobs are stored in `AOS Video Processing Job` and dispatched through the shared transactional outbox after the defining database transaction is durable. The private processing companion uses bounded FFmpeg argument arrays and writes only under server-issued Media-owned object prefixes. Signed callbacks enter `aos.api.internal.video_processing.handle_callback`; Frappe validates generation/idempotency and registers outputs as canonical Media before linking them to a Short.

### State machines

Short lifecycle is independent from technical processing and moderation. Lifecycle values are `Draft`, `Processing`, `Pending Review`, `Published`, `Rejected`, `Hidden`, `Failed`, and `Deleted`. Processing values are `Not Required`, `Queued`, `Processing`, `Retry Waiting`, `Ready`, `Failed`, and `Cancelled`. Moderation values are `Draft`, `Pending`, `Approved`, `Rejected`, and `Hidden`. Public distribution requires `Published`, technically `Ready`/`Not Required`, and `Approved`.

Video Processing jobs use `Queued`, `Processing`, `Retry Waiting`, `Ready`, `Failed`, and `Cancelled`, with a generation, deterministic active key, bounded attempts, exponential retry time, lease owner/expiry, and terminal callback correlation.

## Data Model

`AOS Short` is the canonical post record and uses opaque `SHR-*` IDs. Video Shorts reference raw and derived Media; Photo Shorts use ordered `AOS Short Photo` rows. `AOS Short Mode`, `AOS Short Hashtag`, `AOS Short Ad`, and `AOS Short Mention` normalize distribution metadata. Engagement uses unique relation rows for likes, saves, reposts and comment likes. `AOS Short Feedback` stores private recommendation feedback. `AOS Short View`/`AOS Short Event` store bounded durable signals while Redis holds hot counters. `AOS Short Moderation Decision` is immutable audit history.

Sounds use opaque `SND-*` IDs. `AOS Short Sound` links a post to one canonical Sound without duplicating audio binaries and owns usage-count accounting. One canonical Original Sound is associated with each source Short; reprocessing replaces its derived audio Media through Media while preserving the Sound identity, source-Short attribution, creator attribution, and reuse history.

## Fields

Client-owned mutable Short fields are limited to caption, canonical hashtag/mention/Ad/Sound relationships, audience, Photo ordering/cover, and post permissions. Server-owned fields include owner, IDs, lifecycle, processing/moderation state and generations, classification, counters, processing errors, moderation decisions, derived Media, timestamps, and ranking metadata. `content_type` is `Video` or `Photo`; Content Modes are relation rows and are never trusted from client input.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `create_comment` | POST | Session required | Client |
| `create_segment_reuse_draft` | POST | Session required | Client |
| `create_short` | POST | Session required | Client |
| `create_side_by_side_draft` | POST | Session required | Client |
| `delete_comment` | POST | Session required | Client |
| `delete_short` | POST | Session required | Client |
| `download_short` | POST | Session required | Client |
| `favorite_sound` | POST | Session required | Client |
| `feed_following` | GET | Session required | Client |
| `feed_for_you` | GET | Guest allowed | Client |
| `get_short` | GET | Guest allowed | Client |
| `get_short_metrics` | GET | Session required | Client |
| `get_sound` | GET | Guest allowed | Client |
| `hashtag_shorts` | GET | Guest allowed | Client |
| `like_comment` | POST | Session required | Client |
| `like_short` | POST | Session required | Client |
| `list_comment_replies` | GET | Guest allowed | Client |
| `list_comments` | GET | Guest allowed | Client |
| `list_sounds` | GET | Guest allowed | Client |
| `my_favorite_sounds` | GET | Session required | Client |
| `my_shorts` | GET | Session required | Client |
| `not_interested` | POST | Session required | Client |
| `record_events` | POST | Guest allowed | Client |
| `record_share` | POST | Guest allowed | Client |
| `repost_short` | POST | Session required | Client |
| `retry_processing` | POST | Session required | Client |
| `save_short` | POST | Session required | Client |
| `saved_shorts` | GET | Session required | Client |
| `search_sounds` | GET | Guest allowed | Client |
| `sound_shorts` | GET | Guest allowed | Client |
| `submit_short` | POST | Session required | Client |
| `undo_repost_short` | POST | Session required | Client |
| `unfavorite_sound` | POST | Session required | Client |
| `unlike_comment` | POST | Session required | Client |
| `unlike_short` | POST | Session required | Client |
| `unsave_short` | POST | Session required | Client |
| `update_short` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

The only client namespace is `/api/method/aos.api.v1.shorts.*`. Reads use GET where appropriate and mutations use POST. Large lists use signed keyset/cursor pagination. Explicit actions are used for like/unlike, save/unsave, repost/undo, comment like/unlike, Sound favorite/unfavorite, and Not Interested. Top-level comments and reply threads are independently cursor-paginated. The client never submits ranking weights or a trusted Content Mode.

Video Processing has no client API. The signed processing callback is `/api/method/aos.api.internal.video_processing.handle_callback`. Desk review actions live under `aos.api.internal.shorts` and require authenticated Desk permission.

### Feeds and Content Modes

`feed_for_you` uses an internal recommendation candidate/session pipeline with stable cursor order and bounded creator diversity. Ranking combines watch ratio, completion, rewatches, early skips, likes, comments, saves, reposts, shares, follow-from-content, Not Interested feedback, creator/mode/hashtag/Sound affinity, current Social-follow state, freshness, and content-quality rates. Candidate sessions are Content-Mode scoped; every cached page is re-hydrated through current lifecycle, processing, moderation, creator-account, Social-block, and audience policy before serialization. `feed_following` is recency ordered and derives membership only from hardened Social. Mode filters use the same feed infrastructure. Shop/Geo/Vibes/Learn membership is classifier-owned and derived from the Short content; linked Ads do not force Shop and no location record forces Geo.

### Video and Photo media

Video creation starts from attached `short_video_raw` Media and asynchronously produces `short_video_playback`, `short_video_manifest`, `short_poster`, `short_storyboard`, `short_storyboard_manifest`, optional `short_original_audio`, and private `short_download` Media. HLS renditions and scrub storyboard assets are immutable/cache-friendly. A processing generation replaces prior derived Media through Media's canonical replacement lifecycle, and the processed poster is the default Video cover. Photo Shorts reference one to ten ordered `short_photo` Media objects directly; photos are never encoded as fake videos.

### Downloads and reuse

Video downloads use an asynchronous cached watermarked rendition and never watermark canonical playback. Photo download returns permission-checked canonical Photo Media URLs. Owner access and per-post download policy are enforced server-side. Side-by-side and segment reuse preserve the source relationship/attribution, require source reuse permissions, generate a new derived Video Short, and enter their own moderation lifecycle.

### Sounds

Every Sound returned by the selectable catalog is reusable by the client. Server policy owns status, reuse permission, availability window, attribution, optional rights/license reference, and takedown. There is no commercial/non-commercial product flag.

### Moderation

Automated moderation and manual Desk review share revision/generation correlation. A manual decision advances moderation generation, making an older automated callback a no-op. Review history records decision source, reviewer where applicable, reason, revision and generation. Technical readiness never implies moderation approval.

## Cross-feature Dependencies

Shorts consumes hardened Media, Accounts, Verification, Social, Maps/Localization, Ads, Notifications, Search Ranking and the shared rate-limit/outbox infrastructure. It does not recreate their tables, ownership rules, URLs, follower graph, listing authority, location authority, notification delivery, or storage credentials.

## Transaction / Concurrency Model

Request services do not commit caller transactions. Mutations execute behind operation savepoints, row locks or optimistic `modified` versions as appropriate. Unique database indexes enforce one reaction per user/post, one mode/hashtag/Ad relation, one comment like, one Sound favorite, processing active-key/idempotency invariants, and viewer identity per Short. Retry-sensitive event/feedback mutations treat concurrent unique-index winners as successful no-ops. Content-affecting edits to an approved published Short advance its revision/moderation generation and return it to Draft so stale approval cannot keep altered content distributed; permission-only edits do not require a new moderation pass. Processing and moderation callbacks validate revision/generation; stale callbacks safely no-op/cancel. External jobs are registered through the transactional outbox rather than dispatched before commit.

## Caching

Redis stores bounded, Content-Mode-scoped For You feed sessions, atomic recommendation-event dedupe keys, shared rate-limit state, and hot watch/counter aggregates. Cached feed order is only a ranking snapshot: current SQL/Social/account distribution policy is rechecked during hydration, so privacy, moderation, blocking, or account changes take effect without waiting for cache expiry. Hot-counter dirty membership is versioned and removed after successful reconciliation only when no concurrent writer raced the flush. Keys are namespaced, expiring, and test code must clear any state it creates. Durable SQL state remains authoritative for lifecycle, permissions and unique actions.

## Performance / Scalability

Feeds and lists are cursor-based and bounded. Feed serialization batches Accounts/Verification/Social state, Media URLs, Photos, modes, hashtags, Ads, Sounds and viewer reaction state to avoid per-card N+1 queries. Playback heartbeat traffic is deduplicated and aggregated instead of updating the Short row for every tick. Manual indexes cover feed order, creator lists, modes, Geo/Shop relationships, reactions, comments, Sound use, moderation queues, processing retries/leases and durable metrics. Processing workers have bounded CPU/memory/time/input-size/resolution/duration/protocol behavior and can scale independently from Frappe web nodes.

## Testing

Normal Shorts fixtures remain transaction-local; tests must not commit merely to preserve fixtures. Request rollback tests use savepoints. Tests that intentionally exercise cross-transaction outbox behavior use the repository's documented committed-fixture helper. Redis tests remove their own hot-state. CI validates current DocType JSON, explicit v1 endpoints, rate-limit review coverage, current documentation, absence of obsolete toggles/commercial-Sound fields/public processing callbacks, index installation and the no-manual-request-commit invariant.

### Configuration and operations

Video Processing is configured with its private service URL, dispatch secret, callback secret, queue settings, resource limits and Media storage configuration. The processing service is not exposed as a public client service. Scheduled maintenance activates due retries and stale recovery through durable job/outbox state. Operational metrics cover queue depth, retry/stale counts, processing latency/failure, output sizes, feed latency/candidate counts, Redis failures, moderation backlog and publish failures.
