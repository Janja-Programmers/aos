# Operations

## Required configuration

```text
MINIO_ENDPOINT
MINIO_SECURE
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
MINIO_PUBLIC_BASE_URL
MINIO_PUBLIC_BUCKET
MINIO_PRIVATE_BUCKET
AOS_MINIO_BUCKET
AOS_STORAGE_CONNECT_TIMEOUT_SECONDS
AOS_STORAGE_READ_TIMEOUT_SECONDS
AOS_STORAGE_MAX_RETRIES
AOS_STORAGE_RETRY_BACKOFF_MS
AOS_MEDIA_DOWNLOAD_EXPIRY_MINUTES
AOS_MEDIA_INITIALIZED_RETENTION_HOURS
AOS_MEDIA_UNATTACHED_RETENTION_DAYS
AOS_MEDIA_DELETE_RETRY_HOURS
AOS_MEDIA_CLEANUP_BATCH_LIMIT
AOS_MEDIA_MULTIPART_ACTIVE_LIMIT_PER_USER
AOS_MEDIA_MULTIPART_SESSION_HOURS
AOS_MEDIA_MULTIPART_PART_URL_BATCH_SIZE
AOS_MEDIA_MULTIPART_MAX_PARALLEL_PARTS
MINIO_API_STALE_UPLOADS_EXPIRY
MINIO_API_STALE_UPLOADS_CLEANUP_INTERVAL
```

Production validation requires non-placeholder credentials, a safe public origin, bounded expiry/timeouts/retries, and consistent service configuration. Never put real credentials in source control.

## Readiness

Before traffic:

1. Run deployment/config validators.
2. Confirm MinIO TLS mode matches the endpoint.
3. Confirm public/private/processing buckets exist.
4. Confirm the public bucket is anonymously readable only through the expected object path.
5. Confirm private/staging objects return access denied without a signed URL.
6. Exercise an upload-init/PUT/confirm/public-read flow.
7. Exercise a large `short_video_raw` multipart flow: init, part PUTs, status/resume, complete, Short creation and processing callback.
8. Abort a test multipart session and confirm unfinished part data is reclaimed.
9. Exercise a private verification-document flow and confirm only an authenticated bounded URL is returned.

The operational health endpoint uses the adapter healthcheck and reports only status, bucket counts, public-base configuration, and latency.

## Cleanup jobs

`aos.tasks.media.cleanup_media_objects` applies environment-configured retention and bounded batch size. It processes:

- expired/stale `Initialized` records, including unfinished multipart sessions;
- old unattached `Uploaded`, `Failed`, `Orphaned`, and `Replaced` records;
- retryable `Delete Pending` records;
- staging identities after their signed upload URL expires.

Run through the existing scheduler/worker conventions. Do not invoke raw bucket deletion scripts as a substitute; they cannot safely evaluate feature references.

## Logs and metrics

Stable events include upload initiated/completed/rejected, attachment/replacement, processing started/completed/failed, delete requested/completed/failed, and cleanup completed. Operational metrics include event counts, media bytes, and storage duration using bounded labels (`purpose`, `operation`, `outcome`). Alert on sustained storage-unavailable failures, multipart session-loss/expiry growth, delete-pending growth, processing failure rate, cleanup inactivity, and readiness failure. At scale, also monitor MinIO/S3 request latency, ingress bandwidth, multipart-abort cleanup, and Nginx/edge 4xx/5xx rates for UploadPart requests.

## Incident response

- **Upload incomplete:** verify the staged object and expiry; do not manually mark Uploaded.
- **Public URL wrong:** fix `MINIO_PUBLIC_BASE_URL`/proxy; avoid rewriting object keys.
- **Private leak:** remove public bucket policy immediately, rotate any exposed endpoint credentials, clear private `public_url` caches via migration, and audit access logs.
- **Delete backlog:** restore storage, run cleanup in bounded batches, and verify feature references before manual intervention.
- **Storage/DB divergence:** preserve records, compare known identities with storage inventory, and reconcile through an explicit reviewed script; never mass-delete unknown objects.

## Safe logging

Do not add signed URLs, credentials, user session IDs, private keys, raw object paths, verification document metadata, or filenames to logs/metric labels.
