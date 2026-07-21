# Transactional outbox and durable companion workers

## Architecture

One Frappe transaction writes the domain mutation, durable service-job document, and `AOS Transactional Outbox` row. Shared helpers do not commit. The persisted outbox and scheduled publisher are the recovery source of truth; after-commit enqueue is only a latency optimization.

Durable service types:

- `video_processing`
- `moderation`
- `search_indexing`
- `notification_delivery`
- `analytics_ingestion`

Identities are intentionally separate:

- **Outbox ID:** stable outbox idempotency key.
- **Work ID:** stable durable-job/RQ work identity.
- **Active generation/token:** current authoritative callback correlation.
- **Proposed generation/token:** reserved correlation awaiting companion confirmation.
- **Callback job ID:** deterministic `<stable-work-id>_callback_g<generation>`.

## Work and callback jobs

```text
signed dispatch
  -> deterministic work RQ job
  -> external work
  -> bounded terminal result persisted in Redis
  -> work job completes
  -> deterministic callback RQ job
  -> callback retries independently
  -> Frappe commits domain/job/outbox atomically
```

Callback failure never reruns completed work. The Redis result is persisted before callback scheduling and contains only bounded correlation, terminal result, attempt, heartbeat, callback, and audit evidence.

Production Redis uses AOF, a persistent volume, and `noeviction`. Default durable-result retention is seven days. RQ result TTL and durable-result TTL are configured separately; lifecycle writes never shorten an existing durable TTL.

## Work failure policy

Companion workers classify errors as:

- `retryable_work_error`
- `terminal_work_error`
- `provider_outcome_uncertain`

Retryable errors re-raise while RQ retries remain. No terminal callback is created until work succeeds, fails definitively, or exhausts retries.

A definitive/exhausted failure persists `work_failed`, then schedules a separate failure callback. Once Frappe accepts that callback, the outbox becomes `Completed With Failure`; it is not automatically redispatched.

### Work replay versus callback replay

Callback replay resends an existing terminal result and never reruns work.

Work replay is a separate System Manager action and requires exact identity confirmation:

```bash
bench --site <site> execute aos.tasks.outbox.authorize_terminal_work_replay \
  --kwargs '{
    "outbox_name":"<outbox>",
    "expected_idempotency_key":"<stable-key>",
    "additional_attempts":2
  }'
```

The companion refuses work replay while the old work or callback job is active. It archives the terminal result, removes completed old jobs, resets the same stable work identity, and keeps historical evidence.

## Publisher leases and callback eligibility

Lifecycle status and publisher ownership are independent. The publisher uses:

- `claimed_by`
- `claim_token`
- `claimed_at`
- `lease_expires_at`

It does not change the lifecycle status merely to own the row.

A matching callback remains eligible during a publisher lease, status reconciliation, or recovery HTTP request. Callback validation accepts authoritative active/proposed correlation in:

- `Queued`
- legacy `Claimed`/`Dispatched`
- `Published`
- `Failed`
- `Dispatch Uncertain`
- `Reconciliation Pending`
- callback-resolvable `Manual Review`

Only explicit old-generation, token-mismatch, superseded, or incompatible-terminal evidence permanently stops callback delivery. Temporary serialization/server conflicts remain retryable.

## Dispatch uncertainty

A timeout or reset may happen after the companion accepted the work. Frappe therefore does not mark business work failed. It preserves correlation and enters `Dispatch Uncertain`.

The signed private status endpoint:

```text
POST /internal/jobs/status
```

returns bounded evidence only:

- `queued`
- `started`
- `callback_pending`
- `callback_complete`
- `failed`
- `absent`
- `unknown`

Reconciliation behavior:

- queued/started: preserve the authoritative generation and extend observation;
- callback pending: resume callback delivery without rerunning work;
- callback complete: verify/repair Frappe state;
- absent: enqueue one clean proposed generation using the same durable job;
- newer companion generation: persist the generation floor and converge;
- unknown/unreachable: exponential backoff;
- exhausted uncertainty/reconciliation: `Manual Review` with alerts.

`Manual Review` is nonclaimable but remains callback-resolvable. A valid late signed callback can still complete it atomically.

## Generation convergence

Frappe stores `companion_authoritative_generation` and never proposes below that floor. Reconciliation has its own attempt count, maximum, next time, and last outcome. It cannot repeatedly propose the same stale generation forever.

`duplicate_active` preserves the running generation/token. Proposed correlation is promoted only when the companion confirms that exact generation. A recovery observation does not consume a work attempt.

## Callback-complete repair

If the companion reports callback completion while the outbox is nonterminal, Frappe locks and compares:

- domain status;
- durable service-job status;
- active/completed generation;
- terminal result type/digest;
- callback timestamps.

Consistent local terminal evidence repairs the outbox to `Completed` or `Completed With Failure`. Insufficient or conflicting evidence follows bounded reconciliation and manual review.

## Callback atomicity

Signed callback payload/timestamp validation occurs before one savepoint-bounded transaction. The transaction locks and updates:

- domain object;
- service-job document;
- outbox;
- callback audit fields;
- terminal timestamps.

Exact duplicates are idempotent. Old generations, wrong tokens, superseded replays, and conflicting terminal results mutate nothing. Save failures roll back SQL changes and Frappe transaction callback-manager state.

## Notification uncertainty

Provider timeouts are not blindly resent. Use the signed private endpoint:

```text
POST /internal/jobs/uncertainty/resolve
```

with one decision:

- `confirmed_accepted`
- `confirmed_failed`
- `approved_resend`
- `permanently_unresolved`

Approved resends are bounded and blocked while prior work is active. Provider/operator references are stored only as digests. Unresolved cases alert and can remain in manual review.

## Analytics deduplication

Analytics applies a Redis Lua claim-and-increment operation atomically.

- Explicit `event_id`: globally deduplicated by event type/namespace and event ID, across work jobs and batch positions.
- Missing event ID: retry-stable identity from stable work ID, immutable position, and canonical original payload.

Generated timestamps, retries, callback generations, and RQ execution IDs are excluded from identity. The guarantee is effectively once within the configured dedupe TTL and Redis persistence boundary.

## Legacy normalization

Registered patches:

- `backfill_transactional_outbox`
- `normalize_outbox_recovery_state`
- `separate_worker_callback_lifecycle`
- `finalize_outbox_failure_reconciliation`

They are batched, idempotent, and commit-free. Legacy failure callbacks become `Completed With Failure`; claim statuses normalize back to lease-based lifecycle states; legacy processing jobs reconcile rather than pretending modern correlation; exhausted uncertainty/reconciliation becomes callback-resolvable manual review.

```bash
bench --site <site> migrate
bench --site <site> execute aos.patches.v1_0.finalize_outbox_failure_reconciliation.execute
bench --site <site> execute aos.patches.v1_0.finalize_outbox_failure_reconciliation.execute
```

The second execution must make no additional changes.

## Diagnostics

```bash
bench --site <site> execute aos.tasks.outbox.publish_transactional_outbox \
  --kwargs '{"limit":100}'
bench --site <site> execute aos.services.transactional_outbox.outbox_monitoring_summary
curl -H "Authorization: Bearer $AOS_METRICS_TOKEN" \
  https://<private-api>/api/method/aos.api.metrics.background_jobs
curl -H "Authorization: Bearer $AOS_METRICS_TOKEN" \
  http://127.0.0.1:<service-port>/metrics
```

Investigate retries, callback age, manual review, dead letters, uncertainty, reconciliation, heartbeats, Redis persistence, and durable-result existence before replaying work.

## Exactly-once boundary

Callback retries do not rerun work after durable result persistence. Analytics is effectively once within its dedupe retention. Notification uncertainty avoids automatic duplicate sends.

Strict third-party exactly-once cannot be guaranteed across a crash after provider acceptance but before local durable result persistence unless that provider supplies idempotency or authoritative status reconciliation.
