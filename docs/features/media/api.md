# Media API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `confirm_upload` | POST | Session required | Client |
| `delete_media` | POST | Session required | Client |
| `get_media_url` | Any* | Guest allowed | Client |
| `init_upload` | POST | Session required | Client |
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

## Initialize direct upload

`POST /api/method/aos.api.v1.media.init_upload` — authenticated.

```json
{
  "purpose": "profile_image",
  "filename": "avatar.png",
  "content_type": "image/png",
  "size_bytes": 157,
  "checksum_sha256": "optional-64-character-sha256",
  "idempotency_key": "optional-client-operation-id"
}
```

Success contains `media_id`, a short-lived `upload_url`, required `upload_headers`, `expires_in`, and `expires_at`. The URL points to a server-generated object in the private staging bucket. Bucket and object key are never accepted from the client.

## Upload bytes

Use HTTP `PUT` against `upload_url` with the exact returned headers. A successful PUT is still only a staged upload.

## Confirm upload

`POST /api/method/aos.api.v1.media.confirm_upload` — authenticated.

```json
{"media_id": "MEDIA-2026-00001"}
```

Confirmation locks the record, checks ownership and expiry, stats the staged object, validates expected size, detects the real file type, hashes content, verifies an optional checksum, validates image metadata, promotes to the final bucket/key, and persists the completed state. Repeating a successful completion returns the same completed media and does not duplicate promotion.

Private media returns `url: null`. Public media returns its canonical public URL.

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

Upload limits are owned centrally by `aos/services/media/media_purposes.py` and are enforced during `init_upload`/confirmation rather than left to downstream moderation. In particular, `ad_video` currently accepts `video/mp4` or `video/quicktime` up to **200 MiB** and **300 seconds**, with at most one attached Ad video. Files outside that Media policy should be rejected before upload/attachment.

The moderation companion has a separate byte cap for **image inspection**. That inspection cap is not an Ad-video upload limit: moderation records a bounded video-presence signal without downloading the video into the image/Pillow inspection path.

## Compatibility

Existing v1 method names and top-level response fields remain. Additive fields include checksum, dimensions, attachment state, and expiry metadata. Legacy feature URL fields remain readable but are regenerated from Media identity. Deprecated direct URL inputs for profile/banner/review/ad/live media are rejected to prevent URL injection.

## Stable errors

Common codes include `INVALID_MEDIA_PURPOSE`, `UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `INVALID_FILE`, `INVALID_FILENAME`, `MEDIA_NOT_FOUND`, `MEDIA_NOT_READY`, `MEDIA_ALREADY_ATTACHED`, `MEDIA_OWNERSHIP_REQUIRED`, `MEDIA_ACCESS_DENIED`, `UPLOAD_EXPIRED`, `UPLOAD_INCOMPLETE`, `SIZE_MISMATCH`, `CHECKSUM_MISMATCH`, `STORAGE_UNAVAILABLE`, `RESOURCE_NOT_FOUND`, `MEDIA_LIMIT_EXCEEDED`, and `PROCESSING_FAILED`.
