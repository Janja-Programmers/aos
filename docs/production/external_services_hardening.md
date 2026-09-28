# AOS external services hardening

This document covers the production hardening layer added after the five external-service phases.

## Services covered

| Service | Port | Durable Frappe job DocType | Terminal success statuses |
|---|---:|---|---|
| Video processing | 8130 | AOS Video Processing Job | Ready |
| Content moderation | 8140 | AOS Moderation Job | Allowed, Review Required, Rejected |
| Search / ranking | 8150 | AOS Search Index Job | Indexed, Deleted |
| Notification delivery | 8160 | AOS Notification Delivery Job | Delivered, Skipped |
| Analytics pipeline | 8170 | AOS Analytics Ingest Job | Ingested, Skipped |

Frappe remains the source of truth for business state. External services perform heavy or isolated execution only.

## Health checks

From `/home/aos/aos`:

```bash
./scripts/aos_services_smoke_test.sh
```

Or manually:

```bash
curl http://127.0.0.1:8130/health
curl http://127.0.0.1:8130/ready
curl http://127.0.0.1:8140/health
curl http://127.0.0.1:8140/ready
curl http://127.0.0.1:8150/health
curl http://127.0.0.1:8150/ready
curl http://127.0.0.1:8160/health
curl http://127.0.0.1:8160/ready
curl http://127.0.0.1:8170/health
curl http://127.0.0.1:8170/ready
```

## Frappe status summary

```bash
cd /home/aos/frappe-bench
bench --site aos-staging.duckdns.org execute aos.tasks.service_hardening.external_service_job_status_summary
```

Or in `bench console`:

```python
from aos.tasks.service_hardening import external_service_job_status_summary
external_service_job_status_summary()
```

## Boundary audit

Run this before releases:

```bash
cd /home/aos/frappe-bench
bench --site aos-staging.duckdns.org execute aos.tasks.service_hardening.audit_external_service_hardening
```

It verifies:

- no `frappe.enqueue(..., job_id=...)` calls exist inside the AOS app;
- service API `__init__.py` files remain whitelisted wrappers only;
- current job status counts are visible for all service job DocTypes.

## Cleanup / retention

Scheduled cleanup runs daily:

```python
"aos.tasks.service_hardening.cleanup_external_service_jobs"
```

The default behavior is conservative:

- keep durable job rows;
- prune bulky payload bodies from successful terminal jobs after 30 days;
- prune bulky payload bodies from failed/cancelled jobs after 90 days;
- do not delete job rows unless delete thresholds are explicitly enabled.

Environment variables:

```env
SERVICE_JOB_CLEANUP_ENABLED=true
SERVICE_JOB_SUCCESS_PAYLOAD_RETENTION_DAYS=30
SERVICE_JOB_FAILED_PAYLOAD_RETENTION_DAYS=90
SERVICE_JOB_DELETE_SUCCESS_AFTER_DAYS=0
SERVICE_JOB_DELETE_FAILED_AFTER_DAYS=0
SERVICE_JOB_CLEANUP_LIMIT=500
```

Dry-run cleanup:

```bash
bench --site aos-staging.duckdns.org execute aos.tasks.service_hardening.cleanup_external_service_jobs --kwargs '{"dry_run": true}'
```

Actual cleanup:

```bash
bench --site aos-staging.duckdns.org execute aos.tasks.service_hardening.cleanup_external_service_jobs
```

## Release checklist

1. Run migrations.
2. Restart Frappe workers and web.
3. Recreate any changed external service containers.
4. Run `./scripts/aos_services_smoke_test.sh`.
5. Run `audit_external_service_hardening`.
6. Trigger one representative job per service:
   - create Short upload for video processing;
   - create/update moderated content;
   - run related ads or search/ranking reindex;
   - trigger a notification with an active Android FCM token;
   - send `a normal authorized Ad detail request and verify the resulting `AOS Analytics Ingest Job` reaches `Ingested`.
7. Confirm latest jobs are terminal-success or expected-failure with clear `last_error`.

## Reserved enqueue keyword rule

Use `job_id=` only for the Frappe/RQ Redis job identifier. Do not use it as an application-level argument passed to the task function.

Use feature-specific kwargs for application IDs, and a stable `job_id` for queue deduplication:

```python
frappe.enqueue(
    "aos.tasks.video_processing.dispatch_video_processing_job",
    video_job_id=job.name,
    queue="long",
    enqueue_after_commit=True,
    job_id=f"dispatch-video-processing:{job.name}",
)
```

Accepted application-level kwargs include:

- `video_job_id`
- `moderation_job_id`
- `search_job_id`
- `delivery_job_id`
- `analytics_job_id`
