# AOS Media Storage Audit

Date: 2026-07-02

This audit records the remaining intentional media/file references after the AOS Media Object migration.

## Source-of-truth rule

New AOS-owned uploads must use `AOS Media Object` and must be created through:

- `aos.api.media.init_upload`
- direct PUT to MinIO presigned URL
- `aos.api.media.confirm_upload`

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

## Remaining intentional legacy fields

These fields are retained only as URL caches or legacy compatibility fields:

| Doctype / field | Reason |
| --- | --- |
| `User.user_image` | Frappe/core/mobile compatibility cache for profile image URL. Source of truth is `AOS Profile.profile_image_media`. |
| `AOS Seller.shop_banner` | Cached public seller banner URL. Source of truth is `AOS Seller.shop_banner_media`. |
| `AOS Live Stream.cover_image` | Cached public cover URL for existing live payloads. Source of truth is `live_cover_media`. |
| `AOS Short.thumbnail_url` | Cached public thumbnail URL. Source of truth is `thumbnail_media` for new shorts. |
| `AOS Short.processed_file_url` and `playback_url` | Processed Shorts outputs/HLS paths. These are generated public playback outputs, not raw uploads. |
| `AOS Sound.file_url` | Cached public sound URL. Source of truth is `sound_media` for new uploaded sounds. |
| `AOS Message Attachment.file` | Legacy old chat attachments only. New rows use `media`. |
| `AOS Review Image.image` | Cached public review image URL / legacy fallback. Source of truth is `media`. |
| `AOS Ad Image.image` and `AOS Ad.video` | Cached public URL / legacy fallback. Source of truth is media fields. |
| `AOS Verification Document.attachment` | Legacy/cached field. New verification submissions use `media`. |
| `AOS User Activity.target_image` | Snapshot URL only; fieldtype is `Data`, not `Attach`. |
| `AOS Category.icon` | Cached public category icon URL. Source of truth is `icon_media` for new icons. |

## Remaining intentional `File` references

The only allowed Frappe `File` lookups in AOS-owned APIs are legacy read fallbacks, mostly for old chat attachments that already exist in the database.

New write paths for profile images, seller banners, ads, reviews, verification documents, chat attachments, live covers, shorts raw uploads, thumbnails, and uploaded sounds should not create or attach Frappe `File` records.

## Containment policy

- Do not accept `/files/...` or `/private/files/...` for new AOS-owned user uploads.
- Continue reading old legacy URL fields so old data does not break.
- Keep public URL cache fields for fast UI rendering.
- Private media must be accessed through `aos.api.media.get_media_url`, which returns signed URLs after permission checks.
- For chat attachments, signed URL access must be based on conversation membership, not only media ownership.
