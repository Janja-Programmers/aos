# Storage

## Adapter contract

`StorageAdapter` provides bucket selection, bucket readiness, signed PUT/GET, stat/existence, range and chunk reads, bounded in-memory reads, streamed/byte writes, copy, idempotent delete, canonical public URL generation, and health reporting.

`MediaService` depends on this protocol. Tests inject deterministic in-memory storage; production uses `MinioStorage`.

## Bucket roles

- `MINIO_PUBLIC_BUCKET`: public final objects.
- `MINIO_PRIVATE_BUCKET`: private final objects and all direct-upload staging objects.
- `AOS_MINIO_BUCKET`: existing processing output bucket used by Shorts/video integration.

Purpose policy selects the final class. A client never submits a bucket.

## Object keys

Final keys are generated as:

```text
<purpose-prefix>/<yyyy>/<mm>/<owner-hash>/<random-uuid>.<allowed-extension>
```

Staging keys use:

```text
incoming/<purpose>/<yyyy>/<mm>/<owner-hash>/<random-uuid>.<allowed-extension>
```

User filenames never appear in storage paths. The adapter rejects nulls, backslashes, empty/dot/traversal segments, invalid buckets, and overlong keys.

## Timeouts and retries

`MinioStorage` configures separate connection/read timeouts and disables urllib3's unbounded automatic retry behavior. The adapter performs bounded exponential backoff only for retryable, idempotent operations. Authentication/configuration and invalid-input failures are non-retryable. Default limits are intentionally small and production-validated.

## Public URLs

`MINIO_PUBLIC_BASE_URL` must be a bare HTTP(S) origin with no path/query/fragment. It must resolve through the deployment proxy/CDN to public object paths. Production configuration rejects internal hostnames. The canonical format is:

```text
<public-origin>/<public-bucket>/<object-key>
```

Public URL caches in legacy fields are compatibility-only. Serializers regenerate from Media identity.

## Private URLs

Private downloads use MinIO signed GET URLs after Media authorization. Expiry defaults to `AOS_MEDIA_DOWNLOAD_EXPIRY_MINUTES` and is capped at 60 minutes. Signed URLs are never persisted or logged.

## Bucket policy

The application attempts to apply read-only `s3:GetObject` policy to the public bucket. Deployments may manage policy outside the application; readiness must still validate external public access during deployment smoke testing. Private and staging buckets must never receive public-read policy.

## Reliability behavior

- Missing object on completion becomes `UPLOAD_INCOMPLETE`.
- Missing object on delete is success/idempotent.
- Copy is verified by a destination stat and exact size comparison.
- A failed database write after a new object write triggers compensating deletion.
- A failed delete does not mark metadata deleted.
- Health reports are sanitized and never expose credentials or object paths.
