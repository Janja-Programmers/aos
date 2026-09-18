# Media

## Overview
Media owns the provider-neutral lifecycle for AOS binary objects: authorization, upload initialization, multipart coordination, confirmation, metadata validation, attachment, derived processing state, signed/public delivery, deletion, and reconciliation.

## Responsibilities
Media owns object identity, purpose policy, MIME/magic-byte/dimension/size validation, storage keys, upload/finalization state, attachment limits, public/private delivery semantics, processing jobs, cleanup, and storage-provider abstraction.

## Boundaries
Accounts, Sellers, Catalog, Ads, Verification, Chat, Shorts, Reviews, and Live consume Media objects and URLs. They do not own provider credentials, bucket/key construction, upload confirmation, or duplicate media-validation logic.

## Architecture
```text
Versioned Media API -> MediaService -> purpose policy + repository -> AOS Media Object / Processing Job
                                      -> provider-neutral object storage adapter -> shared object store/CDN
```
Long-running processing and reconciliation run in workers outside the initiating request transaction.

## Data Model
- `AOS Media Object`: canonical durable media record; named `MEDIA-<uuid4hex>` without naming-series allocation.
- `AOS Media Processing Job`: durable derived-processing/reconciliation job state.
- Resource attachments are represented through canonical Media ownership/attachment fields and owning-domain links rather than provider-specific paths.

## Fields
| Model | Field | Required / constraint | Purpose |
|---|---|---|---|
| AOS Media Object | `name` | `MEDIA-<uuid4hex>` PK | Stable opaque media identifier generated independently on every node. |
| AOS Media Object | purpose / visibility | policy constrained | Selects validation, delivery, and attachment policy. |
| AOS Media Object | owner/resource linkage | canonical Links | Authorizes management and attachment. |
| AOS Media Object | object key / provider metadata | internal | Provider-neutral storage identity; never a frontend contract. |
| AOS Media Object | MIME/size/dimensions/state | validated | Canonical confirmed object metadata and lifecycle. |
| AOS Media Processing Job | media/job/status | indexed durable state | Tracks asynchronous processing/reconciliation work. |

## API
The current Media endpoints initialize uploads, coordinate multipart parts/completion/abort, confirm objects, return read URLs, report processing status, and delete media. Required inputs are purpose-specific, strict, and ownership-authorized. Clients upload directly to storage only with server-issued authorization and then confirm through Media.

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `abort_multipart_upload` | POST | Session required | Client |
| `complete_multipart_upload` | POST | Session required | Client |
| `confirm_upload` | POST | Session required | Client |
| `delete_media` | POST | Session required | Client |
| `get_media_url` | GET | Guest allowed | Client |
| `init_upload` | POST | Session required | Client |
| `multipart_part_urls` | POST | Session required | Client |
| `multipart_status` | POST | Session required | Client |
| `processing_status` | POST | Session required | Client |
| `remove_background` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Cross-feature Dependencies
Media consumes authenticated identity/authorization context and is consumed by Accounts, Sellers, Catalog, Ads, Verification and other media-bearing domains. Owning features store Media links; Media owns validation/finalization/serialization of media delivery data.

## Transaction / Concurrency Model
Database state changes are atomic and object state transitions are validated under the relevant request/worker transaction. Multipart confirmation and asynchronous jobs are idempotent where retry is expected. Worker/batch code may commit bounded units deliberately; external object transfer is not held inside a long database lock.

## Caching
Resolved public/signed URL behavior follows visibility and provider policy; durable object state stays in MariaDB and shared infrastructure. No worker-local cache is required for correctness.

## Performance / Scalability
UUID-backed names remove centralized naming-series allocation from Media creation. Direct-to-object-storage upload avoids proxying large bodies through Frappe. Background processing uses durable jobs and bounded retries; production throughput still depends on DB, queues, object storage/CDN, and load tests.

## Testing
Tests under `aos/api/media/tests` plus shared integration/contract tests cover purpose policy, upload/finalization, multipart behavior, processing, cleanup, authorization, indexes, and retries. Fixture cleanup deletes Media/job/file state in dependency order when rollback alone is insufficient.

## Detailed Reference

### Public transport boundary

Every Media v1 wrapper delegates through the platform canonical `aos.api.shared.transport.execute_endpoint` boundary before Media request validation. Frappe's framework-owned `cmd` routing field is removed there; all genuine client fields remain visible to Media so `reject_unknown_fields` can enforce the exact endpoint contract. Media does not implement a parallel transport filter.


This file is the authoritative documentation for the current AOS Media subsystem and its directly related infrastructure.

Media endpoints accept only the canonical field names documented below and reject unsupported request fields with `VALIDATION_ERROR`.

### Overview

Media owns the durable identity, authorization, upload lifecycle, storage identity, verification, attachment lifecycle, URL serialization, asynchronous background-removal processing, deletion and cleanup of AOS-managed binary objects. Feature domains store canonical Media IDs such as `MEDIA-<uuid4hex>` and ask Media to validate or serialize them.

Media does not own Ads, Shorts, Accounts or other feature business rules. It owns only the shared media guarantees those features depend on. Accounts is a current consumer for profile images. Ads consumes `ad_image`/`ad_video`. Shorts consumes `short_video_raw` and generated `short_thumbnail` media.

The application treats MinIO, Hetzner Object Storage and AWS S3 as deployment choices behind one S3-compatible storage contract. Provider names do not appear in Media API contracts or Media IDs.

### Architecture

```text
Client
  -> Media API (auth, rate limit, request validation)
  -> MediaService (purpose/ownership/lifecycle)
  -> S3-compatible object storage
       -> private staging PUT/multipart upload
  -> confirm/finalize
       -> authoritative HEAD/range/read validation
       -> public image canonicalization when applicable
       -> immutable final object key
  -> feature attachment

Media processing request
  -> AOS Media Processing Job
  -> Frappe long queue
  -> private background-removal service
  -> result written through MediaService
  -> processing job Succeeded/Failed/Retry Waiting

Public read
  -> generated configured media delivery origin/CDN + immutable object key
Private read
  -> authorization -> short-lived signed S3-compatible GET
```

No large client upload is proxied through a Frappe web request. Frappe remains the control plane; object storage receives upload bytes directly.

### Supported environments

Development and shared staging use MinIO. Production is configured for Hetzner Object Storage. AWS S3 can later replace Hetzner without changing Media IDs, public API contracts, Accounts/Ads/Shorts references or frontend Media identity.

Storage-provider changes are an infrastructure/configuration/data-copy concern. Media records retain stable object keys and generate public URLs from the active delivery configuration.

### Configuration ownership

#### AOS Settings

Only safe administrator-controlled application policy belongs in the `AOS Settings` singleton. Current Media-related fields are:

| Field | Purpose |
|---|---|
| `media_presigned_upload_expiry_minutes` | Default bounded lifetime for upload authorization. |
| `background_removal_max_image_bytes` | Admin-controlled input byte ceiling for background removal. |
| `background_removal_service_timeout_seconds` | Admin-controlled request timeout applied by the Media worker to the processor. |

The Media purpose registry in `aos/services/media/media_purposes.py` remains authoritative for purpose-specific MIME, extension, byte, dimension, duration, attachment and multipart policy.

#### Deployment environment / site configuration

Infrastructure identity and secrets must not be stored in Desk. Media uses the provider-neutral variables below:

| Variable | Meaning |
|---|---|
| `AOS_OBJECT_STORAGE_ENDPOINT` | S3-compatible API endpoint as `host[:port]`. |
| `AOS_OBJECT_STORAGE_SECURE` | TLS for the storage API connection. |
| `AOS_OBJECT_STORAGE_REGION` | Optional provider region/location used for signing. |
| `AOS_OBJECT_STORAGE_PATH_STYLE` | S3 URL addressing mode. `true` for local MinIO path-style; `false` for Hetzner/AWS virtual-host-style deployments. |
| `AOS_OBJECT_STORAGE_PRESIGN_ENDPOINT` | Browser-reachable S3-compatible origin used when signing upload/private-download requests. |
| `AOS_OBJECT_STORAGE_PUBLIC_BUCKET` | Deployment-owned public bucket. |
| `AOS_OBJECT_STORAGE_PRIVATE_BUCKET` | Deployment-owned private/staging bucket. |
| `AOS_OBJECT_STORAGE_ACCESS_KEY` | Service credential; secret infrastructure config. |
| `AOS_OBJECT_STORAGE_SECRET_KEY` | Service credential; secret infrastructure config. |
| `AOS_OBJECT_STORAGE_MANAGE_BUCKETS` | Development/staging convenience. Must be false in production; production buckets/policies are provisioned by infrastructure. |
| `AOS_MEDIA_PUBLIC_BASE_URL` | Public delivery root. May be a local MinIO public-bucket path in development or a bucket-neutral CDN/media origin in staging/production. |
| `AOS_MEDIA_DOWNLOAD_EXPIRY_MINUTES` | Default private signed-GET lifetime, bounded to 1-60 minutes. |
| `AOS_STORAGE_CONNECT_TIMEOUT_SECONDS` | Storage connection timeout. |
| `AOS_STORAGE_READ_TIMEOUT_SECONDS` | Storage read timeout. |
| `AOS_STORAGE_MAX_RETRIES` | Bounded retries for safe/idempotent storage operations. |
| `AOS_STORAGE_RETRY_BACKOFF_MS` | Storage retry backoff base. |
| `AOS_MEDIA_DELETE_QUEUE_NAME` | Frappe queue for cheap asynchronous object deletion; default `short`. |
| `AOS_MEDIA_DELETE_JOB_TIMEOUT_SECONDS` | Deletion job timeout. |
| `AOS_MEDIA_PROCESSING_QUEUE_NAME` | Frappe queue for expensive Media processing; default `long`. |
| `AOS_MEDIA_PROCESSING_JOB_TIMEOUT_SECONDS` | Processing job timeout. |
| `AOS_MEDIA_PROCESSING_MAX_ATTEMPTS` | Maximum durable processing attempts, bounded 1-10. |
| `AOS_MEDIA_PROCESSING_STALE_SECONDS` | Age after which a stuck `Processing` job may be recovered. |
| `AOS_MEDIA_PROCESSING_RECOVERY_LIMIT` | Bounded reconciliation batch size. |
| `BACKGROUND_REMOVAL_SERVICE_URL` | Private deployment URL of the background-removal service. |
| `BACKGROUND_REMOVAL_SERVICE_SECRET` | Internal bearer credential shared only by Media workers and the processor. |
| `BACKGROUND_REMOVAL_MAX_IMAGE_BYTES` | Processor-side hard request ceiling, independent of the runtime product policy in AOS Settings. |
| `BACKGROUND_REMOVAL_MAX_IMAGE_PIXELS` | Processor-side decoded-pixel ceiling. |
| `BACKGROUND_REMOVAL_MAX_CONCURRENT_INFERENCES` | Per-replica bounded ONNX inference concurrency; default `2`. |
| `BACKGROUND_REMOVAL_INFERENCE_ACQUIRE_TIMEOUT_SECONDS` | Maximum wait for an inference slot before a retryable `BACKGROUND_REMOVAL_BUSY`; default `1`. |
| `BACKGROUND_REMOVAL_CPU_THREADS` | Per-replica OpenMP thread ceiling used by the CPU-only inference runtime; default `2`. |

The MinIO container uses `MINIO_*` deployment variables because MinIO itself is infrastructure. Media application code uses only the provider-neutral `AOS_OBJECT_STORAGE_*` contract.

### Storage contract

`MediaService` depends on `StorageAdapter`. Runtime Media storage is `S3CompatibleStorage`, implemented with S3-compatible operations and configuration. Current operations are:

- bucket selection/readiness;
- presigned direct PUT;
- multipart create, signed UploadPart URLs, authoritative part listing, completion and abort;
- HEAD/stat;
- bounded range/read/chunk iteration;
- put/copy/delete;
- short-lived signed GET;
- generated public delivery URL;
- sanitized health reporting.

The storage adapter contains no Media purpose, account, Ads or Shorts authorization rules. Business authorization remains in `MediaService`.

#### Bucket roles

Public final objects use the configured public bucket. Private final objects and direct-upload staging use the configured private bucket. Clients never submit or choose a bucket.

Production bucket creation/policies are infrastructure-managed. Development/staging may opt into application bucket creation by setting `AOS_OBJECT_STORAGE_MANAGE_BUCKETS=true`. Hetzner production should set `AOS_OBJECT_STORAGE_PATH_STYLE=false`; local MinIO normally uses `true`.

#### Object keys

Canonical object keys are server generated:

```text
<purpose-prefix>/<yyyy>/<mm>/<owner-sha256-prefix>/<random-uuid>.<extension>
```

Direct-upload staging keys are:

```text
incoming/<purpose>/<yyyy>/<mm>/<owner-sha256-prefix>/<random-uuid>.<extension>
```

User filenames are display metadata only. They never become trusted bucket/key paths. Keys are immutable and randomized, which makes public CDN caching safe and avoids overwrite races during replacement.

### CDN and public delivery

Media records persist stable object keys rather than provider or delivery URLs. Public identity is `(Media ID -> configured object key)`. `S3CompatibleStorage.build_public_url` appends the immutable object key to `AOS_MEDIA_PUBLIC_BASE_URL`.

Examples:

```text
local MinIO:
AOS_MEDIA_PUBLIC_BASE_URL=http://127.0.0.1:9100/aos-public

shared staging:
AOS_MEDIA_PUBLIC_BASE_URL=https://staging-media.example.com

production:
AOS_MEDIA_PUBLIC_BASE_URL=https://media.example.com
```

A staging/production CDN or reverse proxy is expected to map that public delivery root to the deployment's public object bucket. The frontend does not need to know the storage provider or bucket.

Immutable keys mean replacements receive new URLs and can use long public cache lifetimes at the CDN/origin layer without purge-driven correctness. Authorization is never delegated to a public CDN cache.

### Public and private purposes

Current purpose policy is centralized in `aos/services/media/media_purposes.py`.

| Purpose | Visibility | Allowed content | Max bytes | Resource / notes |
|---|---|---|---:|---|
| `profile_image` | Public | JPEG/PNG/WebP | 5 MiB | `AOS Profile`, one; 64px minimum, 8000px maximum. |
| `seller_banner` | Public | JPEG/PNG/WebP | 10 MiB | `AOS Seller`, one; minimum 320x120. |
| `ad_image` | Public | JPEG/PNG/WebP | 10 MiB | `AOS Ad`, up to four; 64px minimum. |
| `ad_video` | Public | MP4/QuickTime | 200 MiB | `AOS Ad`, one; maximum 300 seconds. |
| `review_image` | Public | JPEG/PNG/WebP | 10 MiB | `AOS Review`, up to five. |
| `live_cover` | Public | JPEG/PNG/WebP | 10 MiB | `AOS Live Stream`, one. |
| `category_icon` | Public | JPEG/PNG/WebP | 5 MiB | `AOS Category`, one; category write permission. |
| `short_thumbnail` | Public | JPEG/PNG/WebP | 5 MiB | Internal/generated only; `AOS Short`, one. |
| `sound_upload` | Public | MPEG/MP4/AAC/WAV/Ogg audio | 50 MiB | `AOS Sound`, one; maximum 600 seconds. |
| `verification_document` | Private | JPEG/PNG/WebP/PDF | 20 MiB | `AOS Verification Request`, up to ten. |
| `chat_attachment` | Private | image/video/audio/PDF | 50 MiB | `AOS Message`, up to ten. |
| `background_removal_source` | Private | JPEG/PNG/WebP | 10 MiB | Unattached processing source. |
| `short_video_raw` | Private | MP4/QuickTime | 300 MiB | `AOS Short`, one; maximum 600 seconds; processing required; multipart at 16 MiB+. |

Private signed URLs are never stored in DocTypes or logs. Verification-document URLs are additionally capped to ten minutes.

### Data layer

#### `AOS Media Object`

Canonical public names use `MEDIA-<uuid4hex>`, generated independently on each application node. Consumers store/use this opaque Media ID rather than storage keys or URLs.

##### Identity and policy fields

| Field | Meaning / invariant |
|---|---|
| `name` | Canonical opaque Media ID. |
| `owner_user` | Authoritative owning Frappe User; indexed. |
| `purpose` | Immutable logical purpose after initialization; selects server-side policy. |
| `status` | Lifecycle state. |
| `visibility` | `Public` or `Private`; must equal purpose policy. |

##### Storage fields

| Field | Meaning / invariant |
|---|---|
| `bucket` | Deployment bucket selected by server policy. It is not public identity. |
| `object_key` | Canonical immutable final object key; unique. |
| `upload_bucket` | Private staging bucket during direct upload, or active multipart identity where applicable. |
| `upload_object_key` | Server-generated staged upload key/canonical private multipart key. |
| `upload_expires_at` | Upload authorization/session expiry; indexed. |
| `staging_cleanup_required` | Durable marker for cleanup/reconciliation. |
| `upload_mode` | `direct` or `multipart`. |
| `multipart_upload_id` | Private object-store multipart upload ID; never returned/accepted by public APIs. |
| `multipart_part_size_bytes` | Server-selected part size. |
| `multipart_part_count` | Expected part count. |
| `multipart_last_activity_at` | Multipart activity timestamp for bounded cleanup/recovery. |
| `multipart_completed_at` | Storage multipart completion timestamp. |
| `multipart_aborted_at` | Abort timestamp. |

Delivery URLs are generated from the canonical object key and deployment configuration; URL fields are not part of Media persistence.

##### File metadata

| Field | Meaning / invariant |
|---|---|
| `original_filename` | Sanitized display metadata only. Hidden from verification-document public serialization. |
| `content_type` | Server-verified canonical MIME type. |
| `expected_size_bytes` | Client-declared source upload size/contract. |
| `size_bytes` | Canonical final object size. For sanitized public images this may differ from the uploaded source size. |
| `etag` | Storage ETag metadata; not used as public identity. |
| `expected_checksum` | Optional client-provided SHA-256 of the uploaded source. |
| `checksum` | SHA-256 of canonical stored bytes when calculated. Public-image checksum is of sanitized bytes. |
| `width` / `height` | Validated image dimensions when applicable. |
| `duration_seconds` | Authoritative ISO-BMFF duration recorded at finalize for MP4/QuickTime uploads; server-bounded for purposes with duration limits. |

##### Attachment/lifecycle fields

`attached_doctype`, `attached_name`, `attached_field`, `attached_at`, `orphaned_at`, `replaced_at`, `deleted_at`, `delete_requested_at`, `failure_code`, `failure_reason`, `failed_at`, retry/storage-error fields and processing timestamps represent durable lifecycle/reconciliation state. `derived_from_media` links a derivative to its source. `processing_job` uniquely links a generated Media result to the durable processing job that produced it. `replaced_by_media` records replacement lineage.

##### Query indexes / constraints

Canonical schema supplies unique constraints for `object_key` and `processing_job`. Runtime indexes installed through Frappe index APIs support owner/purpose/status lookup, attachments, cleanup scans, upload expiry, delete retry, idempotency lookup, derivative lookup, staging cleanup and active multipart sessions. Cleanup/list scans are bounded.

#### `AOS Media Processing Job`

This DocType is the durable asynchronous processing/outbox record for Media-owned work.

| Field | Meaning / invariant |
|---|---|
| `owner_user` | Owner of source/result; indexed. |
| `source_media` | Source `AOS Media Object`; indexed. |
| `operation` | Current implementation supports `Background Removal`. |
| `result_purpose` | Valid output Media purpose. |
| `status` | `Queued`, `Processing`, `Retry Waiting`, `Succeeded` or `Failed`. |
| `result_media` | Result Media link after success. |
| `request_key` | Unique SHA-256 of owner/source/operation/result-purpose; cross-node idempotency boundary. |
| `attempt_count` / `max_attempts` | Durable bounded retry state. |
| `next_attempt_at` | Retry scheduling/recovery timestamp; indexed. |
| `last_error_code` / `last_error_message` | Sanitized client/operator-visible failure category; no raw processor stderr. |
| `started_at` / `completed_at` | Durable processing timing. |

Composite indexes support owner/status reads, source/operation/status lookups and bounded recovery scans.

### Lifecycle and transaction model

Database and object storage are separate consistency domains. Media deliberately uses state transitions plus reconciliation instead of pretending they are one ACID transaction.

#### Direct upload

```text
Initialized
  -> client PUT to private staging key
  -> confirm
  -> storage verification
  -> immutable final object write/copy
  -> Uploaded
  -> Attached (when a feature consumes it)
```

If final storage write succeeds but the DB save fails, Media attempts compensating deletion of the final object. Staging cleanup remains retryable through durable fields and scheduled cleanup.

#### Processing

```text
AOS Media Processing Job: Queued
  -> Processing
  -> Succeeded
or
  -> Retry Waiting -> Processing
or
  -> Failed
```

The worker commits the `Processing` claim before external I/O so duplicate workers on separate nodes observe it. A result Media row contains a unique `processing_job` link. If derivative creation commits but the final job update is interrupted, retry/recovery finds that result and marks the job succeeded instead of creating a duplicate derivative.

#### Deletion

```text
live/unattached Media
  -> Delete Pending (committed authoritative DB state)
  -> after-commit short-queue deletion
  -> storage identities deleted idempotently
  -> Deleted
```

A storage outage leaves `Delete Pending` and records a bounded storage-error category. Scheduled cleanup retries it. Missing objects are treated idempotently. A referenced/attached object is not deleted.

### Upload authorization and initialization

#### `POST /api/method/aos.api.v1.media.init_upload`

Authentication: required.

Frontend use: call before uploading any client-selected file.

Required fields:

- `purpose` — registered client-uploadable Media purpose;
- `filename` — sanitized display filename, no paths/control chars/dangerous double extension;
- `content_type` — declared MIME; must be allowed by purpose;
- `size_bytes` — positive and within purpose limit.

Conditional/optional fields:

- `duration_seconds` — required up front for processing-oriented video purposes such as `short_video_raw` as an upload policy hint; finalize parses authoritative MP4/QuickTime `moov/mvhd` metadata with bounded range reads, enforces the purpose duration ceiling, and stores the verified duration instead of trusting the client value;
- `upload_mode` — `direct`, `auto` or `multipart`; optional and defaults to `auto`, which selects multipart automatically when the purpose/size requires it;
- `checksum_sha256` — optional 64-character SHA-256 for authoritative source verification;
- `idempotency_key` — optional client retry identity. Reusing the same key for a different upload contract returns `IDEMPOTENCY_CONFLICT`.

Side effects: creates one `Initialized` Media record, selects immutable final/staging keys and creates a multipart session when selected. Duplicate initialization with the same idempotency contract returns the existing row.

Direct response contains `media_id`, `upload_url`, exact `upload_headers`, expiry and limits. Multipart response contains the Media ID as the public session ID plus part size/count and concurrency/batch hints; the raw S3 multipart upload ID is never exposed.

Rate limit: `INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER` through shared Redis-backed rate limiting.

### Finalization

#### `POST /api/method/aos.api.v1.media.confirm_upload`

Authentication: required.

Required input: `media_id`.

Frontend use: call after a successful direct PUT. Multipart completion calls the same canonical confirmation lifecycle internally.

Authoritative checks:

- row lock and uploader ownership;
- valid non-terminal lifecycle state;
- upload not expired;
- object exists at the server-issued upload identity;
- exact staged object length equals initiation contract;
- MIME magic matches declared/allowed type;
- SHA-256 matches optional client checksum;
- images decode successfully and satisfy dimensions/pixel limits;
- PDFs are streamed and reject known active-content constructs;
- source object remains stable while verified.

Public JPEG/PNG/WebP uploads are decoded, EXIF orientation is applied and the image is re-encoded before entering the public canonical key. This strips EXIF/GPS/user metadata and rejects animated/decompression-bomb content. Consequently `expected_size_bytes` remains the upload contract while `size_bytes`/`checksum` describe canonical sanitized bytes.

Idempotency: repeated confirmation after success returns the same Media record and does not duplicate the final object.

### Multipart upload

Multipart is currently enabled where the purpose registry requires it for large raw Shorts video. Frappe remains control-plane only.

#### `POST /api/method/aos.api.v1.media.multipart_part_urls`

Authentication: required. Inputs: `media_id`, optional `start_part`, optional `count`. Ownership and active-session state are checked. Returned batches are bounded and every URL is scoped to one expected object key, upload ID, part number and HTTP PUT operation.

#### `POST /api/method/aos.api.v1.media.multipart_status`

Authentication: required. Input: `media_id`. Returns object-storage-authoritative part state (`uploaded_parts`, `missing_parts`, `invalid_parts`, `retry_parts`, byte progress and `complete_ready`). Clients resume from this server result, not locally remembered ETags.

#### `POST /api/method/aos.api.v1.media.complete_multipart_upload`

Authentication: required. Input: `media_id`. Media row is locked; server lists authoritative object-store parts and verifies contiguous part numbers, non-empty ETags and exact expected part sizes before completion. The client does not submit a trusted ETag manifest. Completion verifies final assembled size then runs the normal Media confirmation path.

If object storage completed but the response was ambiguous/lost, retry detects the assembled object and reconciles the Media state. Repeated completion after success is idempotent.

#### `POST /api/method/aos.api.v1.media.abort_multipart_upload`

Authentication: required. Input: `media_id`. Aborts the server-owned object-store upload ID, removes staging bytes best-effort and records terminal `UPLOAD_ABORTED`. Repeated abort is idempotent. Scheduled cleanup also reclaims abandoned/expired sessions.

Active multipart sessions per user, part counts, part URL batches and client concurrency hints are bounded server-side.

### Read URLs

#### `GET /api/method/aos.api.v1.media.get_media_url`

The endpoint is guest-decorated only so public Media can be resolved without a session. Input: `media_id`; private callers may optionally request `expiry_minutes`, which remains server-bounded.

Public Media: returns the configured CDN/public delivery URL only when the Media row is in a readable state and its purpose is public.

Private Media: requires an authenticated active user authorized by owner/resource rules. Chat attachments additionally require conversation participation. Verification documents allow owner or effective attached-resource read access. Private URL authorization happens in AOS before a short-lived signed URL is produced.

A caller cannot obtain a private signed URL by changing a Media purpose or submitting a bucket/key; those fields are server authoritative.

### Attachment and Accounts integration

Feature services attach through `MediaService.attach_media`/`validate_media_for_use`, not raw DocType assignment. Attachment validates:

- owner/user management permission;
- exact expected purpose;
- purpose visibility;
- allowed target DocType;
- target-resource authorization;
- readable/attachable lifecycle state;
- purpose-specific per-resource count.

The attachment target database row is locked before the Media row. This serializes resource-level item limits across Frappe processes/nodes and prevents two concurrent workers from both passing a count check.

Accounts profile-image integration uses `profile_image`, requires Media ownership, and serializes its public URL from the Media ID. Profile replacement receives a new immutable object key and releases/replaces the prior Media through the Media lifecycle. Accounts public/private projections remain owned by Accounts; Media does not expose User email or storage identity as the public Media contract.

### Background removal

#### `POST /api/method/aos.api.v1.media.remove_background`

Authentication: required. Inputs: `media_id`; optional `result_purpose` (defaults to the source purpose). Source must be an owned readable JPEG/PNG/WebP Media object. Output purpose must be one of the currently allowed image purposes and accept PNG.

The web request does not perform inference. It creates/reuses one durable `AOS Media Processing Job` and queues the job on the configured Media processing queue. The idempotency request key is derived from owner, source, operation and output purpose, so duplicate requests from multiple web nodes return the same job.

Response shape:

```json
{
  "processing": {
    "job_id": "<opaque job id>",
    "operation": "background_removal",
    "status": "Queued",
    "source_media_id": "MEDIA-...",
    "result_purpose": "ad_image",
    "attempt_count": 0,
    "max_attempts": 3,
    "next_attempt_at": null,
    "error": null,
    "media": null
  }
}
```

#### `POST /api/method/aos.api.v1.media.processing_status`

Authentication: required. Input: `job_id`. Only the processing-job owner can read it. When status is `Succeeded`, `media` contains the normal serialized canonical result Media record and URL subject to Media access rules.

Processing failures expose bounded stable error codes, not raw processor exceptions.

#### Processor trust boundary

The Frappe worker downloads only the source object selected by the authoritative Media row, validates it again, then POSTs bytes to the configured private background-removal service. The processor requires `Authorization: Bearer <BACKGROUND_REMOVAL_SERVICE_SECRET>`, compares the secret in constant time, applies byte and decoded-pixel limits, and returns sanitized service errors. It should be reachable only on the internal deployment network/localhost path and is not a public media or storage API.

Background-removal failure never mutates or corrupts the original. Transient service/storage failure enters `Retry Waiting` with exponential bounded backoff. Non-retryable malformed/unprocessable input becomes `Failed`. A periodic recovery job re-enqueues due jobs and reclaims stale `Processing` claims in bounded batches.

### Delete endpoint

#### `POST /api/method/aos.api.v1.media.delete_media`

Authentication: required. Input: `media_id`. Client `force` deletion is rejected.

The caller must manage the Media, the purpose must permit deletion, and the Media must not remain attached or externally referenced. The API commits the authoritative `Delete Pending` state and schedules deletion after commit. The response therefore says deletion is scheduled rather than falsely claiming object storage has already completed.

Duplicate delete calls against `Delete Pending` are safe. Worker/scheduled reconciliation performs idempotent storage deletion and eventually marks `Deleted`.

### MIME/content security

Media does not trust extensions, browser MIME or client metadata in isolation. Initialization checks the declared contract and filename; confirmation checks authoritative object length plus byte signatures and decoded/streamed content.

Current accepted client content is deliberately narrow: JPEG, PNG, WebP, MP4/QuickTime, supported audio formats and PDF according to purpose. SVG/HTML/script/executable extensions are rejected. Unsafe double extensions, path separators, control characters and hidden filenames are rejected.

Image decoding converts Pillow decompression-bomb warnings to hard failures, caps decoded pixels, rejects malformed/animated content and enforces purpose dimensions. Public images are re-encoded without EXIF/GPS metadata.

### Concurrency and retry guarantees

All correctness locks are database/storage based; Media uses no Python process-local lock for business correctness.

Important concurrency boundaries are:

- initialization idempotency serializes final lookup/insert and uses a hashed retry identity;
- confirmation locks the Media row;
- multipart completion/abort/status mutation uses the Media row and authoritative object-store state;
- attachment locks the shared target resource before the Media row;
- delete transitions to committed `Delete Pending` before external deletion;
- processing-job `request_key` is unique across nodes;
- worker claim commits `Processing` before inference;
- generated result `processing_job` is unique, reconciling duplicate worker execution.

Storage retry logic is bounded and only enabled for operations safe to retry. Multipart creation is intentionally not automatically retried after an ambiguous network failure because doing so could create an untracked second upload ID.

### Cleanup and reconciliation

The scheduled Media task performs bounded batches for:

- expired upload/multipart sessions;
- aged unattached/orphan candidates;
- `Delete Pending` storage deletion retries;
- staging-object cleanup.

A separate scheduled task recovers queued/retryable/stale Media processing jobs. Cleanup checks active Media/feature references before deleting canonical bytes. Duplicate cleanup workers rely on row state/locks and idempotent object deletion rather than process-local coordination.

Operational retention values are deployment configuration unless represented by existing safe AOS Settings policy. The system does not invent a public per-account storage quota; abuse is bounded today through purpose limits, endpoint rate limits, active multipart-session caps, bounded processing attempts and bounded recovery scans. Aggregate long-term storage quota remains a product/operations policy decision.

### Rate limiting and abuse resistance

Every public Media endpoint is registered in `ci/public-endpoint-rate-limits.json`. Authenticated endpoints use shared Redis-backed user keys; guest public-URL requests use IP keys. Rate-limit keys are structured through the common digest-safe helper instead of embedding raw sensitive values directly where normalization is required.

Upload, confirmation, multipart URL/status/completion/abort, URL generation, deletion, background removal and processing-status polling each have explicit operation-specific limits. Resource-intensive work is moved to workers.

### Observability

Media emits structured bounded events for upload initialization/completion/rejection, multipart completion/abort, attachment, replacement, deletion request/completion/failure, processing queue/completion/failure/retry and storage failures. Storage health reports contain only categories, counts and latency—not credentials or object paths.

Do not log presigned URLs, query signatures, service secrets, storage credentials, raw private media, verification contents, arbitrary filenames or processor stderr.

### Error contract

All public endpoints use the standard AOS envelope:

```json
{"ok": true, "message": "...", "data": {}}
```

or:

```json
{"ok": false, "message": "...", "error": "STABLE_CODE", "data": null}
```

Current Media errors include `INVALID_MEDIA_PURPOSE`, `UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `DURATION_REQUIRED`, `INVALID_FILE`, `INVALID_FILENAME`, `INVALID_CHECKSUM`, `MEDIA_NOT_FOUND`, `MEDIA_PROCESSING_NOT_FOUND`, `MEDIA_NOT_READY`, `MEDIA_ALREADY_ATTACHED`, `MEDIA_OWNERSHIP_REQUIRED`, `MEDIA_ACCESS_DENIED`, `UPLOAD_EXPIRED`, `UPLOAD_INCOMPLETE`, `UPLOAD_ABORTED`, `MULTIPART_REQUIRED`, `MULTIPART_NOT_SUPPORTED`, `MULTIPART_ACTIVE_LIMIT`, `MULTIPART_ALREADY_COMPLETED`, `MULTIPART_INCOMPLETE`, `MULTIPART_PART_SIZE_MISMATCH`, `MULTIPART_SESSION_LOST`, `IDEMPOTENCY_CONFLICT`, `SIZE_MISMATCH`, `CHECKSUM_MISMATCH`, `STORAGE_UNAVAILABLE`, `BACKGROUND_REMOVAL_FAILED`, `BACKGROUND_REMOVAL_UNAVAILABLE`, `RESOURCE_NOT_FOUND`, `MEDIA_LIMIT_EXCEEDED` and `INVALID_STATE`.

Raw S3/MinIO/Hetzner/AWS exceptions, bucket credentials, internal hosts, object keys for private media, SQL, paths and processor tracebacks are not public errors.

### Docker / infrastructure

The repository Compose keeps MinIO for development/staging. MinIO stores `/data` in the named `minio_data` volume so container recreation does not discard objects. Its API and console bind to `127.0.0.1` by default, both have explicit ports, health checks, restart policy and resource/pid limits. Browser upload CORS is explicit and must not be broad anonymous bucket write access; clients upload with presigned operations.

The background-removal container is an immutable, offline-capable inference appliance. It uses its own digest-pinned Python 3.13 runtime because the reviewed rembg stack is validated independently from the Frappe/other companion-service Python 3.14 runtime. The reviewed U2Net model is downloaded only during image build, verified against the repository manifest, stored below the current `REMBG_HOME=/opt/aos/rembg` layout, made root-owned/read-only, and re-verified by SHA-256 before an ONNX session is created. There is no mutable model volume and no runtime model selector or first-request model download. The container runs as UID/GID 10001 with a read-only root filesystem, all Linux capabilities dropped, and only `/tmp` plus the Numba JIT cache exposed as ephemeral tmpfs. Docker health uses `/ready`, so a replica is not healthy until the bundled model is usable. Inference concurrency is bounded per replica and saturation returns retryable `BACKGROUND_REMOVAL_BUSY`. Media workers remain responsible for fetching/storing Media objects.

Production should not expose the MinIO admin console, should set `AOS_OBJECT_STORAGE_MANAGE_BUCKETS=false`, should pre-provision object buckets/CORS/policy and should point `AOS_OBJECT_STORAGE_*` at Hetzner. `AOS_MEDIA_PUBLIC_BASE_URL` should point at the production CDN/media domain. The same application contract supports AWS S3 later.

### Accounts integration contract

Accounts remains production-ready alongside Media. The Media integration boundary preserves:

- canonical profile Media ID ownership;
- exact `profile_image` purpose;
- another account's Media cannot be attached;
- concurrent replacement is serialized by Accounts/profile locking plus Media attachment locking;
- missing/deleted/non-ready new Media is rejected through existing Accounts error mapping; a missing previous avatar reference can be repaired by replacement/removal under the locked Profile transaction;
- public avatar serialization derives a URL from the Media ID/configuration;

### Ads and Shorts boundary

Media supplies the current Ads and Shorts storage/lifecycle requirements without owning either feature's business rules:

- Ads: up to four `ad_image` objects and one bounded `ad_video`, immutable public delivery, attachment authorization/count enforcement.
- Shorts: private raw video, direct multipart upload for large objects, processing state support and public generated thumbnails.

Other features consume these canonical Media contracts instead of adding provider-specific storage logic.

### Tests and verification

Pure Media validation/policy/runtime tests cover file signatures, malformed images, public-image re-encoding/metadata stripping, purpose rules and configuration bounds. Frappe Media service tests use an in-memory storage adapter and cover initialization idempotency, multipart behavior, authoritative confirmation, public-image canonicalization, ownership/IDOR, private signed access, attachment authorization, deletion retry and expired staging cleanup. DocType tests cover schema/storage/ownership invariants. Background-removal service tests cover internal authentication, input bounds, processor success/failure and safe exception/validation responses.

The repository validators that must remain clean are:

```bash
python ci/validate_api_documentation.py
python ci/validate_doc_paths.py
python ci/validate_rate_limit_coverage.py .
python ci/validate_repository.py .
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q aos infra
```

In a supported Python 3.14 Bench environment, also run focused Media/Accounts/Auth/Localization regressions followed by:

```bash
bench run-tests --app aos
```

Compose must additionally pass the repository's pinned `ci/validate-compose.sh`/`docker compose config` validation in an environment with Docker Compose available. A validation must not be reported as passing unless it actually ran.
