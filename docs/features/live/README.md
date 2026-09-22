# AOS Live + LiveKit

This document is the canonical current-state backend and infrastructure reference for AOS Live. Live is the client-facing product domain. LiveKit is shared, server-controlled RTC infrastructure consumed by Live and Calls.

## Domain boundaries

**Live owns product behavior:** Live creation and lifecycle, host/co-host/viewer authorization, discovery, viewer sessions, messages/replies, reactions, counters, Social block enforcement, Media-backed covers, Notifications fanout, and the client-facing endpoint that returns a role-scoped RTC session.

**Shared LiveKit infrastructure owns RTC primitives:** server configuration, room administration, signed token generation, grants, participant identity limits, webhook verification, room/participant reconciliation primitives, TURN/ICE networking, Redis-backed multi-node routing, health, and bounded external-call behavior. Shared code lives in `aos/services/livekit/` and `aos/services/livekit_service.py`; it is not a public feature API.

Calls consumes the same shared room-administration and token primitives. Live hardening does not change Calls lifecycle semantics.

Authentication, Accounts, Social, Verification, Media, Notifications, Chat sharing, and generic moderation/reporting remain owned by their established hardened domains. Live consumes those contracts rather than duplicating them.

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `activate_live_cohost` | POST | Session required | Client |
| `add_live_message` | POST | Session required | Client |
| `cancel_live_cohost` | POST | Session required | Client |
| `delete_live_message` | POST | Session required | Client |
| `end_live` | POST | Session required | Client |
| `end_live_cohost` | POST | Session required | Client |
| `get_live` | GET | Guest allowed | Client |
| `get_live_cohost` | GET | Session required | Client |
| `get_live_cohost_token` | POST | Session required | Client |
| `get_live_token` | POST | Session required | Client |
| `invite_live_cohost` | POST | Session required | Client |
| `join_live` | POST | Guest allowed | Client |
| `list_live_cohosts` | GET | Session required | Client |
| `list_live_messages` | GET | Guest allowed | Client |
| `list_live_replies` | GET | Guest allowed | Client |
| `list_live_streams` | GET | Guest allowed | Client |
| `reply_live_message` | POST | Session required | Client |
| `request_live_cohost` | POST | Session required | Client |
| `respond_live_cohost` | POST | Session required | Client |
| `send_reaction` | POST | Session required | Client |
| `share_live_to_chat` | POST | Session required | Client |
| `start_live` | POST | Session required | Client |
| `track_join` | POST | Guest allowed | Client |
| `track_leave` | POST | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->


The public surface is the versioned Live client API only. LiveKit administration, webhooks, reconciliation, health, diagnostics, and worker operations are internal infrastructure and are not client APIs.

## Persistent model and indexes

`AOS Live Stream` is authoritative for product lifecycle. Client-visible Live IDs are opaque `LIVE-<32 lowercase hex>` identities and the server derives the immutable room name as `live:<live_id>`.

Important consistency fields are internal/read-only: `active_host_key`, `last_livekit_room_event_at`, `room_provision_attempts`, `last_room_error`, `room_cleanup_pending`, and `last_reconciled_at`. `active_host_key` provides a database uniqueness boundary so one host cannot own concurrent `starting`/`live` reservations across workers.

`AOS Live Stream View` stores durable viewer sessions. `active_identity_key` enforces one active session identity and `last_livekit_event_at` provides a monotonic callback watermark. Co-host workflows and message idempotency use their own unique consistency keys. LiveKit webhook event IDs and Notification dedupe keys are database-unique.

Query indexes cover feed order, host state, cleanup/reconciliation, viewer presence and LiveKit identity, webhook ordering, co-host state, cursor message/reply pages, and webhook cleanup. Schema indexes are installed only after bounded data canonicalization.

## Lifecycle

Canonical states are:

```text
starting -> live -> ended
    |        |
    +------> failed is not allowed
    |
    +------> ended
    +------> failed
```

More precisely, valid transitions are `starting -> live|ended|failed` and `live -> ended`; `ended` and `failed` are terminal. Only `live` has `is_active=1`, a `started_at` timestamp, and is joinable/token-eligible.

`start_live` creates a `starting` reservation and enqueues room activation after commit. It does **not** return RTC credentials while provisioning. `aos.tasks.live.activate_live_room` validates host availability, confirms LiveKit room creation, re-locks/revalidates application state, then transitions to `live`. A room-creation failure terminalizes the Live as `failed`; an ambiguous external timeout marks cleanup pending because the room may have been created before the response was lost.

Start and end are idempotent under retries. Competing starts serialize on the User row and the unique active-host key. End uses the canonical Live row lock; terminal states cannot be reopened by API calls, reconciliation, or webhooks. Room cleanup is independent, retryable state so a LiveKit outage cannot resurrect application state.

## Tokens and permissions

All LiveKit access tokens are server-generated. Clients cannot choose room name, participant identity, role, ownership, or grants.

Participant identities are opaque HMAC-derived values. Live participant metadata contains only public account projection needed by the RTC client; it does not use email/internal User values as authority.

Roles use least privilege:

- **host**: room join, publish, subscribe, and data permission;
- **cohost**: room join, publish, subscribe, and data permission only after an accepted/active canonical co-host workflow;
- **viewer**: room join and subscribe only; no media/data publishing grant.

Live token TTL defaults to 15 minutes and is capped at 30 minutes. Calls retains its shorter shared-service cap. Token refresh revalidates current application state, account availability, Social blocks, and the caller-owned active viewer/co-host session before issuance.

Secrets are read from runtime configuration. Full tokens and LiveKit secrets must never be logged.

## Host, co-host, and viewer authorization

Host authority comes from `AOS Live Stream.host_user`, never client metadata. Host/co-host token issuance checks canonical server state while holding lifecycle-safe locks.

A Live supports **one host plus up to five simultaneous co-hosts/guests**. Accepted co-host workflows reserve one of the five slots before activation, so concurrent accepts cannot overbook the room. Public Live payloads expose `active_cohosts[]`, `active_cohost_count`, `has_active_cohosts`, and `cohost_slots` (`limit`, `reserved`, `available`). The singular `active_cohost` compatibility shape is not part of the current contract. Feed serialization batches these projections across the page to avoid per-cohost profile queries.

Host invitations identify the candidate using the server-issued opaque LiveKit identity of an active viewer. The backend resolves and re-locks that view session; raw target User IDs or viewer session IDs are not accepted as an escalation path.

Viewer access consumes hardened Accounts and Social policy. Authenticated disabled/deleted/unavailable accounts are rejected, and bidirectional block relationships are enforced. Guest viewers receive only viewer grants and are protected by both IP and session-oriented rate budgets.

Participant removals are server-side room administration operations. Account deletion/unavailability and reconciliation can queue removal without exposing LiveKit administration to clients.

## Verified webhooks

The only public-facing webhook route is the internal signed LiveKit callback endpoint. It is not a client API.

Webhook processing:

- bounds request size before reading the body;
- verifies the LiveKit signature with the configured API key/secret using the LiveKit SDK verifier;
- stores a unique event ID plus payload hash for idempotency/replay detection;
- rejects reuse of an event ID with a different payload;
- treats duplicate completed delivery as idempotent success and retryable in-flight duplication as retryable failure;
- processes supported room/participant events inside a savepoint and rolls back domain mutation, callbacks, and outbox intent on failure;
- records unknown verified event types without trusting or applying them;
- validates room and participant identity against canonical AOS state instead of trusting webhook metadata;
- stores monotonic room/view event timestamps and ignores equal-or-older delayed participant events;
- never lets `room_started` create or activate a Live;
- lets `room_finished` terminalize canonical `starting`/`live` state but never resurrect terminal state.

Webhook retries are safe. Reconciliation is the final authority when an event is ambiguous, delayed, lost, or conservatively ignored.

## Live discovery and pagination

The existing Live discovery/feed is retained. Public list/read paths use bounded queries and signed keyset cursors with deterministic ordering. Visibility, active state, account availability, and Social blocks are enforced server-side. Creator/account projection is batched to avoid per-item profile queries.

Messages, replies, and co-host listings likewise use cursor pagination rather than offset scans. Client input allowlists reject obsolete pagination/request fields.

## Messages and reactions

Live owns its lightweight room comments/replies; it does not duplicate Chat conversations. Messages are durably stored, payload-size constrained, rate limited, idempotency-protected, visibility/deletion aware, and published only after database commit.

Reactions are transient high-frequency events. They are atomically aggregated in site-namespaced Redis hashes and periodically materialized into `AOS Live Stream.reaction_count`. The Live row is the durable aggregate; request/reconciliation paths do not scan a per-reaction SQL event table. Realtime reaction publication is sampled/bounded independently of accepted reaction counting.

If Redis is unavailable, reaction writes fail with a bounded service-unavailable response rather than degrading into one database insert per tap. This protects MariaDB during a hot-room/cache outage.

## Scaling and counters

Viewer, comment, and reaction hot state uses Redis atomic operations. Durable viewer-session rows preserve join/leave/watch history, while hot aggregate updates are coalesced into short-queue jobs instead of upgrading every shared lifecycle request to a parent-row write.

Viewer-count realtime fanout is coalesced. Reconciliation rebuilds viewer/comment materialized metrics from durable viewer/message state. Reaction aggregates are materialized by coalesced short-queue jobs and again during terminal reconciliation; the production Redis backing Frappe cache must therefore use persistent/HA, no-eviction storage so accepted but not-yet-materialized reaction deltas survive process restarts. A total loss of that Redis can only recover the last materialized reaction count, so this deployment property is part of the production contract rather than a database-per-tap fallback.

Hot-path fallbacks are bounded. Redis failure does not trigger `COUNT(*)` on every viewer join/leave, and reaction failure does not trigger SQL-per-tap persistence. Large terminal view sets are finalized in batches and rediscovered by reconciliation if enqueueing fails.

Follower `live_started` Notifications are bounded background fanout through the hardened Notifications domain and database dedupe key. A notification dependency failure does not roll back a successfully activated Live.

## Infrastructure and networking

The Compose deployment pins `livekit/livekit-server:v1.13.7`. Signaling/API port 7880 is bound to localhost and is expected to be TLS-proxied by Nginx. RTC TCP and UDP mux ports are exposed directly because ICE traffic cannot be treated as ordinary HTTP reverse-proxy traffic.

LiveKit is configured with Redis so multiple LiveKit nodes share distributed routing state. The bundled Redis service is suitable for local/staging/single-host operation; production multi-node deployment should use a managed or independently highly available Redis topology and configure `LIVEKIT_REDIS_*` accordingly. Redis TLS uses the nested `redis.tls` configuration expected by LiveKit v1.13.x.

RTC uses LiveKit's single UDP mux port on `7882` in the Compose baseline; `rtc.udp_port` is a scalar mux port, not a range. TCP fallback defaults to 7881. For horizontally scaled nodes, give each node direct RTC reachability (prefer host networking/orchestrator-native deployment); deployments that deliberately use a UDP allocation range must use LiveKit's `rtc.port_range_start`/`rtc.port_range_end` settings instead. Embedded TURN defaults to UDP 3478 and TURN/TLS 5349 so it does not collide with the repository's Nginx TCP 443 listener. A dedicated TURN public IP/domain or L4 load-balancer may intentionally expose TURN/TLS on 443 for restrictive networks. TURN uses a dedicated directory containing real `fullchain.pem`/`privkey.pem` files and a per-participant relay-allocation cap.

`use_external_ip` is enabled by default for NAT/cloud hosts. Do not expose wildcard bind addresses for signaling/metrics. Prometheus metrics remain host-local. Strict request/signaling metadata/body limits, room participant limits, resource limits, restart policy, graceful stop, JSON sampled logging, and health checks are configured in Compose.

For horizontally scaled LiveKit, run multiple nodes against the same production Redis, preserve direct RTC reachability for each node, and use the configured `sysload`/`twochoice` node selector. Compose does not hard-code a container name so an orchestrated/multi-node deployment is not coupled to one singleton name.

## Configuration

Required secrets/identity:

- `LIVEKIT_KEYS`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_ENDPOINT`
- `LIVEKIT_WEBHOOK_URL`
- `LIVEKIT_TURN_DOMAIN`
- `LIVEKIT_TURN_CERT_DIR`

Operational tuning is exposed through the documented `.env.example` variables for Redis address/auth/TLS, room limits/timeouts, UDP mux port, node load threshold, TURN ports/allocation cap, metrics bind, graceful-stop window, and container CPU/memory/PID limits.

API key/secret pairs used by Frappe token generation/webhook verification must correspond to the key pair supplied to LiveKit. Never commit real credentials. Redis certificate verification must remain enabled in production (`LIVEKIT_REDIS_TLS_INSECURE=false`).

## Failure semantics

Critical and best-effort dependencies are intentionally separated:

- room creation failure: Live never becomes joinable and is terminalized/reconciled;
- token generation failure: no token is returned; lifecycle state is unchanged;
- LiveKit/room cleanup failure: application terminal state remains terminal and cleanup stays pending;
- Redis hot-state failure: protected transient operations fail or use bounded materialized fallback; no unbounded SQL storm is introduced;
- webhook processing failure: savepoint rollback + retry response;
- notification/activity/realtime side effects: run after/around the canonical transition with retry/reconciliation where appropriate and never grant authority themselves;
- Media cover validation/attachment: uses hardened Media purpose/ownership checks and cannot be replaced by a raw client URL.

## Operations and reconciliation

`aos.tasks.live.reconcile_live_state` is the bounded multi-worker-safe recovery scheduler that runs every five minutes. It selects bounded `starting`, active, and terminal-cleanup candidates and dispatches deduplicated per-Live short-queue jobs, so LiveKit network I/O never stretches one scheduler database transaction across many rooms. Co-host expiry and one bounded terminal-view batch remain local database work.

Reconciliation can:

- retry or complete `starting` room provisioning;
- verify/recreate an active room when canonical Live state still requires it;
- delete orphan/terminal rooms;
- close missing or newly unauthorized participant sessions;
- queue room participant removal;
- expire co-host workflows;
- finalize terminal viewer rows in bounded batches;
- materialize final viewer/comment/reaction counts.

External room administration has bounded timeout and retry/backoff. Create/delete/remove operations are idempotent for already-exists/not-found outcomes. Reconciliation re-reads canonical state after an external operation before persisting success so an in-flight room call cannot overwrite a concurrent end.

Structured Live observability records lifecycle transition outcome, external dependency category/latency, webhook failure/duplicate/stale behavior, reconciliation and cleanup anomalies, and queue/dependency failures without logging access tokens, secrets, raw private identifiers, or message bodies.

## Tests

Live coverage includes pure source/contract guards plus Frappe database/API tests. Regression coverage protects:

- `starting -> live` only after confirmed room creation;
- no RTC token while `starting` or after room-provision failure;
- valid/terminal state transitions and host uniqueness;
- shared LiveKit administration boundary and Calls imports;
- viewer/co-host least-privilege token grants;
- webhook verification, dedupe, rollback, and monotonic stale-event protection;
- cursor-only bounded read surfaces;
- Social/account authorization;
- Redis-backed hot counters and no SQL-per-reaction fallback;
- bounded finalization/reconciliation;
- schema/index presence and patch ordering/idempotency;
- rate-limit registry coverage;
- production LiveKit Redis/TURN/network configuration;
- repository hygiene and absence of service-layer commits.

Normal feature fixtures remain transaction-local. Tests must not commit synthetic Users, Accounts, Lives, Media, or room state. Real LiveKit is not required for deterministic domain tests; room administration is mocked except in intentionally isolated infrastructure verification.
