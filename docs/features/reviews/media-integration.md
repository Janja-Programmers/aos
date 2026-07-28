# Media integration

Review images use Media purpose `review_image` only. The central Media policy enforces public image types, extensions, content verification, dimensions, 10 MiB per image and a maximum of five images per review.

Reviews accepts canonical `MEDIA-*` identifiers only. Raw URLs, duplicate identifiers, cross-user Media, wrong-purpose Media and Media that is not ready are rejected. Attachments use `MediaService.attach_media`; edits and withdrawals use `MediaService.release_media`. Reviews never accesses MinIO or object keys directly.

Public URLs are generated from the canonical Media service at serialization time. Internal bucket/object keys are never returned.
