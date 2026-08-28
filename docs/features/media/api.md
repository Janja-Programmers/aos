# Media API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `abort_multipart_upload` | POST | Session required | Client |
| `complete_multipart_upload` | POST | Session required | Client |
| `confirm_upload` | POST | Session required | Client |
| `delete_media` | POST | Session required | Client |
| `get_media_url` | Any* | Guest allowed | Client |
| `init_upload` | POST | Session required | Client |
| `multipart_part_urls` | POST | Session required | Client |
| `multipart_status` | POST | Session required | Client |
| `remove_background` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

All endpoints return the canonical AOS envelope:

```json
{"ok": true, "message": "...", "data": {}}
```

or:

```json
{"ok": false, "message": "...", "error": "STABLE_CODE", "data": null}
```

Clients must branch on `error`, not message text.

## Initialize upload

`POST /api/method/aos.api.v1.media.init_upload` — authenticated.

```json
{
  "purpose": "short_video_raw",
  "filename": "short.mp4",
  "content_type": "video/mp4",
  "size_bytes": 188743680,
  "duration_seconds": 598.4,
  "upload_mode": "auto",
  "checksum_sha256": "optional-64-character-sha256",
  "idempotency_key": "client-operation-id"
}
```

`upload_mode` accepts `auto`, `direct`, or `multipart`. Multipart-capable clients should explicitly send `auto`. The server selects multipart for purposes that support it once the configured threshold is reached. To make rollout safe, a legacy client that omits `upload_mode` is allowed to keep the direct contract only for small files; if the file is above the multipart threshold it receives `MULTIPART_REQUIRED` before any bytes are uploaded. For `short_video_raw`, duration is required and is checked together with the 300 MiB / 600-second limit **before any upload URL is issued**.

A small/direct upload returns `upload_mode: "direct"`, `upload_url`, `upload_headers`, and the normal URL expiry. A large Short returns `upload_mode: "multipart"`, no whole-object PUT URL, and a descriptor such as:

```json
{
  "media_id": "MEDIA-2026-00001",
  "upload_contract_version": 1,
  "upload_mode": "multipart",
  "upload_url": null,
  "multipart": {
    "contract_version": 1,
    "session_id": "MEDIA-2026-00001",
    "part_size_bytes": 8388608,
    "part_count": 23,
    "part_url_batch_size": 8,
    "max_parallel_parts": 4,
    "part_url_expires_in": 3600,
    "session_expires_in": 86400
  }
}
```

The public resumable-session id is the Media id. Raw MinIO multipart upload ids are server-owned and are never accepted as client input.

## Direct upload bytes and confirmation

For `upload_mode: "direct"`, use HTTP `PUT` against `upload_url` with the exact returned headers, then call:

`POST /api/method/aos.api.v1.media.confirm_upload`

```json
{"media_id": "MEDIA-2026-00001"}
```

Confirmation locks the record, checks ownership and expiry, stats the staged object, validates expected size, detects the real file type, verifies an optional checksum, validates image metadata, promotes to the final bucket/key, and persists the completed state. Processing-oriented large video is not synchronously re-downloaded through Frappe merely to calculate SHA-256 when no client checksum was requested. Repeating a successful completion returns the same completed media and does not duplicate promotion.

## Multipart/resumable upload

Large `short_video_raw` files use direct-to-object-storage multipart upload. Frappe handles only control-plane requests; video bytes do not traverse application workers.

### Fetch/resume status

`POST /api/method/aos.api.v1.media.multipart_status`

```json
{"media_id": "MEDIA-2026-00001"}
```

The response is authoritative object-storage state and includes `uploaded_parts`, `missing_parts`, `invalid_parts`, `retry_parts`, byte progress, `storage_complete`, `complete_ready`, and current concurrency/batch hints. `retry_parts` is the exact union of missing and invalid-size parts and is the preferred resume driver. Clients should call this when starting or resuming a multipart session instead of trusting locally remembered successful PUTs.

### Issue part URLs

`POST /api/method/aos.api.v1.media.multipart_part_urls`

```json
{
  "media_id": "MEDIA-2026-00001",
  "start_part": 1,
  "count": 8
}
```

The server returns a bounded batch of short-lived signed UploadPart URLs. Each entry contains `part_number`, `expected_size_bytes`, and `upload_url`. The client uploads **exactly** the byte range for that part. Upload no more than the returned `max_parallel_parts` hint concurrently. Refresh URLs by calling this endpoint again; do not persist them as durable session state.

The client does not send ETags to AOS. On status/completion, the server lists parts from object storage and validates contiguous part numbers, authoritative ETags, and exact expected part sizes. This prevents the API from trusting forged completion manifests.

### Complete multipart upload

`POST /api/method/aos.api.v1.media.complete_multipart_upload`

```json
{"media_id": "MEDIA-2026-00001"}
```

Completion row-locks the Media record, lists and validates all authoritative storage parts, completes the storage multipart session, validates assembled size/magic bytes, and immediately runs the normal Media confirmation lifecycle. Private heavy Shorts assemble directly at their canonical private raw-video key, avoiding a redundant full-object copy after upload. It is retry-safe: if object storage completed but the request died before Frappe persisted completion, the next completion call detects the assembled object and heals the record. After success, the client proceeds directly to `aos.api.v1.shorts.create_short`; it does not need a separate `confirm_upload` call.

### Abort multipart upload

`POST /api/method/aos.api.v1.media.abort_multipart_upload`

```json
{"media_id": "MEDIA-2026-00001"}
```

Abort invalidates the object-store multipart session, removes staged bytes best-effort, and marks the Media upload failed with `UPLOAD_ABORTED`. Repeating an abort is idempotent. Expired/abandoned sessions are also closed by server cleanup.

### Client retry contract

A production client should retain only `media_id`, `upload_contract_version`, and its local file identity/fingerprint. On reconnect it calls `multipart_status`, requests URLs for `retry_parts`, re-uploads those exact parts, rechecks authoritative status, and calls `complete_multipart_upload` when `complete_ready` is true. It must start a new session when the server reports `UPLOAD_EXPIRED` or `MULTIPART_SESSION_LOST`. Reusing an `idempotency_key` for different file metadata is rejected with `IDEMPOTENCY_CONFLICT` rather than silently binding the new file to an old upload session.

Private media returns `url: null`. Public media returns its canonical public URL after confirmation.

## Get media URL

`GET /api/method/aos.api.v1.media.get_media_url?media_id=MEDIA-...` — guest allowed only for public media.

Public media returns the canonical public URL. Private media requires an authorized active user and returns a bounded signed download URL. Requested expiry is capped at 60 minutes and defaults to `AOS_MEDIA_DOWNLOAD_EXPIRY_MINUTES`.

## Delete media

`POST /api/method/aos.api.v1.media.delete_media` — authenticated.

```json
{"media_id": "MEDIA-2026-00001"}
```

The caller must own the media or have effective Write permission on `AOS Media Object`, the purpose must permit deletion, and no attachment or feature reference may remain. `force` is rejected for clients. A repeated successful delete is idempotent. A storage outage leaves the record `Delete Pending` for retry and returns `STORAGE_UNAVAILABLE`.

## Remove background

`POST /api/method/aos.api.v1.media.remove_background` — authenticated, expensive-operation rate limit.

```json
{
  "media_id": "MEDIA-2026-00001",
  "result_purpose": "ad_image"
}
```

The source must be an owned, completed image. Output purposes are explicitly allowlisted. The result is a new Media object linked through `derived_from_media`; source bytes are not overwritten.

## Important purpose limits

Upload limits are owned centrally by `aos/services/media/media_purposes.py` and are enforced during `init_upload`/confirmation rather than left to downstream moderation. In particular, `short_video_raw` accepts up to **300 MiB** and **600 seconds**, requires the duration hint at initialization, and receives a 60-minute PUT window. `ad_video` currently accepts `video/mp4` or `video/quicktime` up to **200 MiB** and **300 seconds**, with at most one attached Ad video. Files outside that Media policy should be rejected before upload/attachment.

The moderation companion has a separate byte cap for **image inspection**. That inspection cap is not an Ad-video upload limit: moderation records a bounded video-presence signal without downloading the video into the image/Pillow inspection path.

## Compatibility

Existing v1 method names and top-level response fields remain. Additive fields include checksum, dimensions, attachment state, and expiry metadata. Legacy feature URL fields remain readable but are regenerated from Media identity. Deprecated direct URL inputs for profile/banner/review/ad/live media are rejected to prevent URL injection.

## Stable errors

Common codes include `INVALID_MEDIA_PURPOSE`, `UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `DURATION_REQUIRED`, `MULTIPART_REQUIRED`, `MULTIPART_NOT_SUPPORTED`, `MULTIPART_ACTIVE_LIMIT`, `MULTIPART_INCOMPLETE`, `MULTIPART_PART_SIZE_MISMATCH`, `MULTIPART_SESSION_LOST`, `IDEMPOTENCY_CONFLICT`, `INVALID_FILE`, `INVALID_FILENAME`, `MEDIA_NOT_FOUND`, `MEDIA_NOT_READY`, `MEDIA_ALREADY_ATTACHED`, `MEDIA_OWNERSHIP_REQUIRED`, `MEDIA_ACCESS_DENIED`, `UPLOAD_EXPIRED`, `UPLOAD_INCOMPLETE`, `SIZE_MISMATCH`, `CHECKSUM_MISMATCH`, `STORAGE_UNAVAILABLE`, `RESOURCE_NOT_FOUND`, `MEDIA_LIMIT_EXCEEDED`, and `PROCESSING_FAILED`.
