# Shorts resumable upload client contract (v1)

This document is the frontend integration contract for the backend-owned Shorts upload lifecycle. It intentionally describes **what a client must do**, while storage credentials, MinIO multipart upload ids and completion ETags remain server-owned.

## Admission before bytes

For `purpose=short_video_raw`, the client must inspect the selected local file before calling `init_upload` and provide truthful metadata:

- `filename`
- `content_type` (`video/mp4` or `video/quicktime`)
- exact `size_bytes`
- numeric `duration_seconds`
- `upload_mode=auto`
- a new idempotency key for that logical file-selection/upload operation

Backend v1 accepts Shorts up to **300 MiB** and **600 seconds**. It rejects oversized/over-duration/unsupported-container requests before issuing any byte-upload capability. The client should mirror these limits for immediate UX, but the backend remains authoritative.

The duration value is an admission hint, not a trust boundary. The processing companion probes the uploaded video again before transcoding and can fail a file whose actual media metadata violates the processing policy.

## Initialize

Call:

`POST /api/method/aos.api.v1.media.init_upload`

Example:

```json
{
  "purpose": "short_video_raw",
  "filename": "clip.mp4",
  "content_type": "video/mp4",
  "size_bytes": 188743680,
  "duration_seconds": 598.4,
  "upload_mode": "auto",
  "idempotency_key": "local-operation-uuid"
}
```

Persist only durable AOS/local state:

- `upload_contract_version`
- `media_id`
- the local file identity/fingerprint needed to prove the user is resuming the same selected file
- local UI progress as a hint only

Do **not** persist signed upload URLs, MinIO multipart upload ids or ETags.

If the same idempotency key is retried with different file metadata, the backend returns `IDEMPOTENCY_CONFLICT`. A client must not work around that error by binding the old session to the new file; create a new logical upload operation/idempotency key for the newly selected file.

## Direct mode

When `upload_mode=direct`:

1. PUT the exact file bytes to `upload_url` using all returned `upload_headers`.
2. On successful PUT, call `aos.api.v1.media.confirm_upload` with `media_id`.
3. After confirmation succeeds, call `aos.api.v1.shorts.create_short` with that `media_id`.

Direct mode is intended for files below the multipart threshold. Large Shorts explicitly require a multipart-capable client and return `MULTIPART_REQUIRED` rather than silently falling back to a fragile single PUT.

## Multipart mode

When `upload_mode=multipart`, `media_id` is the resumable session id. The `multipart` descriptor supplies `part_size_bytes`, `part_count`, `part_url_batch_size`, `max_parallel_parts`, URL expiry and session expiry.

### Start or resume

Always begin/re-enter the session with:

`POST /api/method/aos.api.v1.media.multipart_status`

Inspect `state` first:

- `uploading` — upload `retry_parts`.
- `storage_completed` — call `complete_multipart_upload`; this heals an interrupted completion request.
- `completed` — media confirmation already succeeded; continue to Short creation.
- `failed` — do not upload more parts. Inspect `failure_code`; start a new upload only when appropriate.

`retry_parts` is the exact union of missing parts and wrong-sized parts. It is the authoritative list to re-send. Do not infer resume state only from local HTTP success records.

### Request part URLs

Call `multipart_part_urls` in bounded windows. For each returned part:

- byte offset = `(part_number - 1) * part_size_bytes`
- body length must equal `expected_size_bytes`
- PUT only that byte range to that part's signed `upload_url`
- do not exceed `max_parallel_parts` simultaneous PUTs

A retry of the same part number overwrites that part in the active multipart upload. Signed part URLs are ephemeral: request fresh URLs after expiry or an authorization/signature failure instead of persisting them.

### Retry policy

For byte PUTs, retry transient network failures, connection resets/timeouts, HTTP 408, 429 and 5xx with capped exponential backoff plus jitter. Before an extended retry loop or after connectivity changes, call `multipart_status` again so storage truth wins over local assumptions.

For AOS control-plane calls, honor `RATE_LIMITED`/429 and the server's retry behavior. Do not create a second session merely because one request timed out: first retry `init_upload` with the same idempotency key or query the known `media_id`.

### Complete

Once `multipart_status.complete_ready` is true, call:

`POST /api/method/aos.api.v1.media.complete_multipart_upload`

with only `media_id`. The client does not submit an ETag manifest. AOS lists the authoritative storage parts, checks continuity and exact sizes, completes the object, validates assembled size/type, and confirms the Media record.

Completion is retry-safe. If object storage assembled the object but the API request/DB commit was interrupted, status reports `storage_completed` and a subsequent completion call heals the Media lifecycle.

After successful multipart completion, do **not** call `confirm_upload` separately. Proceed to `aos.api.v1.shorts.create_short`.

## Cancellation, expiry and lost sessions

On explicit user cancellation, call `abort_multipart_upload` best-effort. The backend marks the Media record terminal and asks object storage to remove the unfinished multipart upload.

On `UPLOAD_EXPIRED` or `MULTIPART_SESSION_LOST`, the old session must not be reused. Start a new `init_upload` operation. AOS cleanup plus MinIO stale-upload cleanup independently reclaim abandoned multipart part data.

## App lifecycle expectations

A production mobile client should be able to survive:

- app background/foreground transitions
- app process death
- Wi-Fi/mobile-data switches
- individual part timeout/failure
- expired signed part URLs
- duplicate init/complete requests
- a lost completion response

The durable recovery anchor is `media_id` plus the same local-file fingerprint. Progress shown to the user should be reconciled from `multipart_status` after recovery.

## Processing lifecycle

Upload completion means the raw Media object is durable; it does not mean the Short is ready for playback. `create_short` queues the video-processing job. The frontend should display a processing state until the Short becomes `ready` or `failed`.

The video processor independently probes actual duration, byte size, codec, dimensions and aspect ratio before expensive transcodes. This is deliberate defense in depth: pre-upload hints save bandwidth and improve UX, while server-side probing prevents falsified metadata from bypassing media policy.
