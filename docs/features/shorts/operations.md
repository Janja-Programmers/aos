# Operations

## Required configuration

Configure MinIO credentials/base URL/buckets, video service URL, request/callback secrets, and a separate `SHORT_CLASSIFICATION_SECRET` shared only by video processing and image search. In staging/production set `VIDEO_CALLBACK_ALLOWED_HOSTS` and HTTPS callback URLs. Review all `VIDEO_MAX_*`, timeout and FFmpeg thread limits before enabling workers.

## Scheduled work

Hourly: ranking and daily-metric aggregation. Daily: `aos.tasks.shorts.maintain_short_integrity`, plus canonical Media and external-service cleanup.

## Observability

The `aos.shorts` logger records operation category, outcome, bounded reason and numeric metrics only. Never add account/Short IDs, keys, URLs, captions, comments, searches, cursors, tokens or signed URLs to these logs. Companion logs report failure categories rather than FFmpeg paths/output.

## Staging commands

```bash
bench --site <site> backup --with-files
bench --site <site> migrate
bench --site <site> clear-cache
bench restart
bench --site <site> run-tests --app aos --module aos.api.shorts.tests.test_api_contracts
bench --site <site> run-tests --app aos --module aos.api.shorts.tests.test_database_contracts
bench --site <site> run-tests --app aos --module aos.tests.test_dynamic_sql_safety
bench --site <site> run-tests --app aos
pytest infra/video-processing
pytest infra/image-search
pytest infra/moderation
pytest infra/notification-delivery
pytest infra/analytics-pipeline
```

Verify upload, signed frame classification, automatic Learn/Geo/Vibes assignment, owned-ad Shop assignment, classifier fallback, callback, ready playback, All/vibes parity, all audiences, both block directions, replayed events, delete/retry races and cleanup metrics.

## Migration transaction boundary

Shorts hardening uses two consecutive post-model-sync patches. Data reconciliation completes and is committed by Frappe before the dedicated index patch starts. The index patch contains no inserts, updates, deletes, explicit commits, or rollbacks; this prevents MariaDB's implicit DDL commit from splitting domain-data changes. If index installation fails, fix the reported duplicate/schema condition and rerun `bench --site <site> migrate`; completed indexes are detected and skipped.

## Video worker retry scheduler

The video worker uses RQ interval retries for processing and callback delivery. It must run with the scheduler component enabled:

```text
rq worker --with-scheduler <queue> --url <redis-url>
```

Without the scheduler, a temporary callback failure leaves the retry in RQ's scheduled registry and the Short can remain in `processing`. After changing the worker command, rebuild and recreate `video-worker`; restarting only the Frappe processes is insufficient.

### Stale search-index work after Short deletion

Search-index jobs and transactional outbox rows are durable audit records and may outlive the Short they reference. A missing Short is therefore not treated as a link-validation failure:

- an undispatched stale upsert is converted to an idempotent delete;
- an already accepted upsert is cancelled and replaced with a fresh delete correlation;
- job and outbox lifecycle saves tolerate a deleted optional aggregate while retaining the durable job link;
- dead-letter rows created by an older deployment can be explicitly requeued after confirming their persisted idempotency key.

## Curated sound upload in Desk

Open **AOS Sound → New**, choose the source type, then use **Upload audio**. The browser uploads directly to object storage and the Sound controller finalizes the Media attachment when the document is saved. For `commercial` sounds, `Commercial Safe` is enforced automatically. Existing audio cannot be replaced; create a new Sound instead.

After deployment, run `bench --site <site> migrate` to synchronize the new Desk HTML field, then clear cache and reload Desk assets.

## Recovering a stuck sound remix

A ready Short with `audio_mix_status=pending` or `processing` must have an active
`AOS Video Processing Job` whose reason is `audio_reprocess`. The scheduled
reconciler repairs missing durable work every five minutes.

Immediate staging recovery:

```bash
bench --site <site> execute \
  aos.tasks.shorts.recover_pending_audio_mixes \
  --kwargs '{"stale_minutes":0,"limit":100}'

bench --site <site> execute \
  aos.tasks.outbox.publish_transactional_outbox \
  --kwargs '{"limit":100}'
```

Then verify the video worker receives an `audio_reprocess` job and the Short
moves from `pending` to `processing` and finally `ready` or `failed`.


Audio-mix recovery distinguishes fresh active work from stale work. It can cancel stale active remix generations and atomically create a new generation; an explicit `stale_minutes=0` run forces immediate operator recovery.

For callback troubleshooting, the video worker logs only the callback service, HTTP status, and stable error category. A temporary HTTP response is retried by RQ; no callback body, Short ID, signed URL, or token is logged.
