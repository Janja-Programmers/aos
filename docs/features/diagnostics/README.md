# Diagnostics

## Overview

Diagnostics is AOS's operational-health boundary. It observes whether the application and its required or optional infrastructure can operate safely; it does not own product, user, moderation, analytics, activity, chat, call, or commerce state. The production design separates process **liveness**, cheap **traffic readiness**, and deeper **operational health** so load-balancer probes cannot trigger expensive service fan-out.

Diagnostics is an operator concern. AOS has **no public/client Diagnostics API**, no Postman collection requirement, and no customer-facing Diagnostics UI. The only unauthenticated HTTP methods owned by this area are minimal infrastructure probes for an orchestrator/load balancer:

- `GET /api/method/aos.api.health.liveness`
- `GET /api/method/aos.api.health.readiness`

They intentionally expose only `ok` and `status`. Detailed reports are server-side/bench interfaces.

## Responsibilities

Diagnostics owns:

- process liveness;
- web-node traffic readiness;
- canonical dependency state and required/optional classification;
- redacted operational health;
- Frappe queue, worker, scheduler and durable-job/backlog visibility;
- backup/restore readiness visibility;
- production configuration readiness;
- bounded probes of storage and feature-service adapters;
- operator-facing failure isolation and safe summaries.

## Boundaries

The ownership split is strict:

- **Diagnostics** = operational health, dependencies, queues/backlogs and failures.
- **Analytics** = product/user telemetry and aggregates.
- **Activity** = durable user-facing activity/history.

Diagnostics never copies feature business logic, repairs feature state, replays jobs, changes configuration, or exposes private payloads. Feature-specific readiness remains close to the owning adapter/service; Diagnostics only consumes its safe health contract.

## Architecture

There are three layers:

1. **Liveness** — `aos.utils.operational_health.validate_liveness()`. Pure process response, no database/Redis/network checks.
2. **Traffic readiness** — `validate_readiness()`. Cheap required checks only: MariaDB, Frappe cache Redis and Frappe queue Redis. This is the only dependency work performed by the load-balancer readiness endpoint and by dependency metrics.
3. **Operational health** — `validate_operational_health()`. Operator-triggered deep report that adds production configuration, object storage, LiveKit, Maps and configured companion services. HTTP service probes run concurrently with a strict per-request timeout.

Job health is separately observed through `aos.utils.job_monitoring.validate_job_monitoring()`: bounded service-job queries, RQ queue registries, registered workers, scheduler state, recent background errors and the transactional outbox. Backup and configuration validators remain their own operator utilities and are composed rather than duplicated.

The health model lives in `aos.utils.health_model`. No module-local/global health result is authoritative; every report is an observation of shared state at execution time.

## Data Model

Diagnostics introduces no business DocType and persists no health truth. It reads bounded operational state from existing infrastructure and feature-owned job DocTypes. This is deliberate for HA: no node-local cache, lock, flag, or filesystem marker is used as the canonical health state.

## Fields

Every canonical dependency check has the same safe shape:

- `name`: stable service/check identifier;
- `category`: broad infrastructure domain;
- `requirement`: `required` or `optional`;
- `status`: `healthy`, `degraded`, `unhealthy`, `disabled`, or `unknown`;
- `condition`: `available`, `unavailable`, `misconfigured`, `timeout`, `disabled`, or `unknown`;
- `message`: reviewed non-sensitive operator text;
- `details`: explicitly selected non-secret counters/flags only.

Summary output reports counts plus required blockers and optional impairment. A required `unhealthy`, `unknown`, or `disabled` check blocks that report's readiness. Optional failure degrades the operational report without making application traffic readiness false. An intentionally disabled optional dependency is `disabled`, not a failure.

## API

### Infrastructure probes

`aos.api.health.liveness` and `aos.api.health.readiness` are infrastructure endpoints, **not client/product APIs**. Both are GET-only and guest-accessible so a load balancer can probe a node before a user session exists.

Liveness always performs no dependency check. Readiness executes only the three cheap core dependency checks. It returns HTTP `200` when ready and HTTP `503` when not ready. Neither endpoint returns check names, URLs, exception messages, stack traces, credentials, configuration, user data, or other infrastructure detail.

### Operator-only detailed interfaces

Detailed diagnostics are intentionally server-side:

```bash
bench --site "$SITE" execute aos.utils.operational_health.liveness_summary
bench --site "$SITE" execute aos.utils.operational_health.readiness_summary
bench --site "$SITE" execute aos.utils.operational_health.operational_health_summary
bench --site "$SITE" execute aos.utils.job_monitoring.job_monitoring_summary
bench --site "$SITE" execute aos.utils.backup_readiness.backup_readiness_summary
bench --site "$SITE" execute aos.utils.production_config.production_config_summary
```

Deployment/assertion helpers may raise to fail an operator command or CI gate. They are not repair actions and are never called by liveness/readiness.

## Cross-feature Dependencies

Operational health consumes, but does not own, these contracts:

| Dependency | Application requirement | Probe source | Effect when impaired |
|---|---|---|---|
| MariaDB | required | bounded `SELECT 1` | traffic not ready |
| Frappe cache Redis | required | Redis `PING` | traffic not ready |
| Frappe queue Redis | required | Redis `PING` | traffic not ready |
| Object/media storage | required for deep production health | two configured bucket-existence probes | operational health unhealthy |
| Production configuration | required for deep production health | existing validator | operational health unhealthy |
| Video Processing / FFmpeg | optional to whole-app traffic | companion `/ready`; companion validates Redis + ffmpeg/ffprobe presence | operational health degraded |
| Moderation provider(s) | optional to whole-app traffic | companion `/ready` | operational health degraded or disabled |
| Search Ranking / Qdrant path | optional to whole-app traffic | owning companion readiness (image-search includes Qdrant) | operational health degraded or disabled |
| Analytics pipeline | optional to whole-app traffic | companion `/ready` | operational health degraded or disabled |
| Notification delivery | optional to whole-app traffic | companion `/ready` plus safe Firebase configuration check | operational health degraded or disabled |
| LiveKit | optional to whole-app traffic | private/admin service health | operational health degraded |
| Maps / Photon / Valhalla | optional to whole-app traffic | configured service probes | operational health degraded or disabled |
| Translation / background removal / text safety | optional to whole-app traffic | companion `/ready` | operational health degraded or disabled |
| Frappe workers / scheduler / queues | required for job-health report | RQ/Frappe shared state | job monitoring unhealthy/degraded |

A feature can be optional to *whole-application traffic* while still being required for the feature that owns it. Diagnostics does not weaken the feature's own admission rules.

## Transaction / Concurrency Model

Diagnostics is read-only. No check begins a transaction for mutation, creates records, triggers retries, creates buckets, replays jobs, repairs state, or takes a distributed/local correctness lock.

Deep HTTP dependency probes use a bounded thread pool solely to cap wall-clock fan-out. Results are emitted in deterministic service-plan order, not completion order. Correctness never depends on the pool, sticky sessions, or one node.

Service-job monitoring queries only active statuses and recent failure windows rather than lifetime/full-table status aggregation. Samples are capped at five records. RQ checks use queue/registry cardinalities rather than enumerating job payloads.

## Caching

No health result is cached as authoritative truth. Infrastructure probes observe current shared state. Existing networking connection pools may be reused, but no process-local cached result decides readiness.

Prometheus dependency metrics use only shallow traffic readiness and therefore do not cause a scrape to fan out to external providers. Deep operational health is operator-triggered.

## Performance / Scalability

The design assumes multiple Frappe web nodes, workers and schedulers against shared MariaDB/Redis/storage plus external services.

- Liveness is constant-time and dependency-free.
- Readiness performs three constant/bounded probes and no table scan.
- External HTTP probes have a capped timeout and bounded concurrency.
- Object-storage health checks exactly the configured public/private buckets with a dedicated short-timeout client; it does not list all buckets, retry with backoff, create buckets, or change policy.
- Job SQL is status/time-window bounded, with indexed job status fields and capped samples.
- Queue diagnostics observe cardinalities and bounded registries.
- No health endpoint triggers business recovery.

These properties support HA operation and reduce probe-amplification risk. They do **not** prove capacity for one million users; that requires production-like load, soak, failover and chaos testing.

## Liveness

Liveness means only that the Frappe application process can execute the method. A broken external service, database, worker, scheduler, or feature does not make the process itself non-live. This prevents dependency outages from causing restart storms.

## Readiness

Readiness means the current web node can safely accept normal application traffic. It checks MariaDB plus both Frappe Redis roles and nothing deeper. Expensive companion, provider, storage, backlog or backup checks never run on every load-balancer probe.

## Operational Health

Deep operational health composes required core/config/storage checks with optional feature-service checks. Each failure is isolated: one exception produces a safe check state instead of aborting unrelated probes. HTTP timeouts are classified `unknown`/`timeout`; explicit bad responses are `unhealthy`, while optional deliberate disablement is `disabled`.

## Queue, Worker and Scheduler Health

`validate_job_monitoring()` observes enabled service-job DocTypes, configured RQ queues, worker registration, scheduler active state, recent Error Log signals and transactional-outbox backlog/recovery state. It does not inspect job payloads or full tracebacks. Failed-job counts and samples are recent-window bounded; active-job checks are status bounded.

## Security and Redaction

Public infrastructure probes expose only `ok` and `status`. Detailed reports remain local/operator interfaces. Diagnostics must never emit passwords, API keys, tokens, Redis/MariaDB credentials, signed-service secrets, credential-bearing URLs, filesystem secret contents/paths, user email/phone data, private chat/call content, moderation/report evidence, or raw stack traces.

Deep HTTP responses are reduced to an allowlist of reviewed scalar health fields. URLs are sanitized before any safe detail is included. Provider exception text is not copied into reports. Server logs should record a safe service/check identifier and category rather than request bodies or credentials.

## Timeouts and Failure Isolation

External probe timeout defaults to 3 seconds and is capped at 10 seconds. A timeout becomes an explicit non-healthy state for that dependency; it does not raise through the whole report. Deep probes are concurrent under a bounded worker count so sequential timeout multiplication is avoided.

The object-storage health client uses the health timeout directly with SDK retries disabled. Readiness never calls external HTTP or object storage.

## Docker / Service Healthchecks

Docker Compose healthchecks use bounded intervals/timeouts/retries and service-specific readiness where dependency gating depends on it. In particular, MinIO uses `/minio/health/ready`, and the video-processing `/ready` contract validates Redis plus presence of both `ffmpeg` and `ffprobe`. Companion APIs continue to own their dependency-specific readiness checks.

No health command contains credentials. `depends_on: condition: service_healthy` is used only where startup dependency health is meaningful and does not replace runtime retry/failure handling.

## HA Behavior

All authoritative observations come from shared database/Redis/object-store/service state. There is no local health-truth global, local lock, sticky-session dependency, or local file used for cross-node correctness. Each web node is independently ready or unready for its core dependencies; deep reports describe the dependencies visible from the node executing them.

## Failure Handling

A failed probe is data, not control-flow for unrelated checks. Malformed responses, timeouts, misconfiguration and unavailability have distinct safe conditions. Required blockers make the owning report unready; optional failures degrade it. Diagnostics never catches a failure only to report a misleading healthy state.

## Testing

Coverage includes canonical required/optional/disabled semantics, liveness/readiness behavior, HTTP 503 readiness, redaction, malformed/timeout service responses, optional degradation, required storage failure, bounded job monitoring, worker/scheduler health, metrics hot-path isolation, endpoint architecture and Docker readiness contracts.

Tests use mocks/temp directories rather than durable user/business records wherever possible. Any temporary filesystem/config override is cleaned in teardown/context managers. Server-side Frappe tests must remain order-independent.

## Real-server Verification

After deployment/migration, run at minimum:

```bash
cd ~/frappe-bench
SITE="aos-staging.duckdns.org"

bench --site "$SITE" migrate
bench --site "$SITE" run-tests --app aos

bench --site "$SITE" execute aos.utils.operational_health.liveness_summary
bench --site "$SITE" execute aos.utils.operational_health.readiness_summary
bench --site "$SITE" execute aos.utils.operational_health.operational_health_summary
bench --site "$SITE" execute aos.utils.job_monitoring.job_monitoring_summary
bench --site "$SITE" execute aos.utils.backup_readiness.backup_readiness_summary
```

Also verify the reverse proxy/load balancer uses liveness/readiness rather than deep operational health, exercise one required-dependency outage and one optional-service outage, validate worker/scheduler/backlog alerts, and run production-like load/soak plus dependency-failure/latency chaos tests before making any million-user capacity claim.
