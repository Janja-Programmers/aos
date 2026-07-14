# AOS Media Storage Audit

Date: 2026-07-02

This audit records the remaining intentional media/file references after the AOS Media Object migration.

## Source-of-truth rule

New AOS-owned uploads must use `AOS Media Object` and must be created through:

- `aos.api.v1.media.init_upload`
- direct PUT to MinIO presigned URL
- `aos.api.v1.media.confirm_upload`

Feature payloads should submit `media_id` / feature-specific media fields, not `/files/...` URLs or Frappe `File` names.

## Current media purposes

| Purpose | Visibility | Main consumer |
| --- | --- | --- |
| `ad_image` | Public | Ads |
| `ad_video` | Public | Ads |
| `review_image` | Public | Reviews |
| `seller_banner` | Public | Seller profile |
| `live_cover` | Public | Live |
| `profile_image` | Public | Account profile |
| `category_icon` | Public | Catalog categories |
| `chat_attachment` | Private | Chat messages |
| `verification_document` | Private | Verification requests |
| `background_removal_source` | Private | Background removal processing |
| `short_video_raw` | Private | Shorts processing input |
| `short_thumbnail` | Public | Shorts feed/listing |
| `sound_upload` | Public | Shorts sounds |

## Remaining generated URL/cache fields

These fields are retained only as generated URL caches or playback outputs. They are not accepted as upload input:

| Doctype / field | Reason |
| --- | --- |
| `User.user_image` | Frappe/core/mobile compatibility cache for profile image URL. Source of truth is `AOS Profile.profile_image_media`. |
| `AOS Seller.shop_banner` | Cached public seller banner URL. Source of truth is `AOS Seller.shop_banner_media`. |
| `AOS Live Stream.cover_image` | Cached public cover URL for existing live payloads. Source of truth is `live_cover_media`. |
| `AOS Short.thumbnail_url` | Cached public thumbnail URL. Source of truth is `thumbnail_media` for new shorts. |
| `AOS Short.processed_file_url` and `playback_url` | Processed Shorts outputs/HLS paths. These are generated public playback outputs, not raw uploads. |
| `AOS Sound.file_url` | Cached public sound URL. Source of truth is `sound_media` for new uploaded sounds. |
| `AOS Message Attachment.file` | Deprecated hidden field. New rows require `media`. |
| `AOS Review Image.image` | Cached public review image URL generated from `media`. |
| `AOS Ad Image.image` and `AOS Ad.video` | Cached public URLs generated from media fields. |
| `AOS Verification Document.attachment` | Deprecated hidden field. New rows require `media`. |
| `AOS User Activity.target_image` | Snapshot URL only; fieldtype is `Data`, not `Attach`. |
| `AOS Category.icon` | Cached public category icon URL. Source of truth is `icon_media` for new icons. |

## Frappe `File` boundary

AOS-owned media APIs do not use Frappe `File` for business uploads. Frappe may still use `File` internally for framework/admin needs, but custom AOS upload flows use `AOS Media Object` only.

## Containment policy

- Do not accept `/files/...` or `/private/files/...` for new AOS-owned user uploads.
- Keep public URL cache fields for fast UI rendering, but generate them from media objects.
- Private media must be accessed through `aos.api.v1.media.get_media_url`, which returns signed URLs after permission checks.
- For chat attachments, signed URL access must be based on conversation membership, not only media ownership.
