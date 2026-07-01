"""Purpose-based media upload rules for AOS.

Every AOS-owned upload must declare a purpose. This registry decides:
- public/private bucket selection
- object key prefix
- visibility
- allowed MIME types
- maximum size

Keep infrastructure values out of this file. Buckets come from .env through
`aos.utils.aos_config.get_minio_config()`.
"""

from __future__ import annotations

from dataclasses import dataclass


IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

VIDEO_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/x-m4v",
}

AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/aac",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
}

DOCUMENT_TYPES = {
    "application/pdf",
}


@dataclass(frozen=True)
class MediaPurpose:
    key: str
    bucket_type: str
    prefix: str
    visibility: str
    allowed_content_types: frozenset[str]
    max_size_bytes: int

    @property
    def is_public(self) -> bool:
        return self.visibility == "Public"

    @property
    def is_private(self) -> bool:
        return self.visibility == "Private"


MEDIA_PURPOSES: dict[str, MediaPurpose] = {
    # Ads
    "ad_image": MediaPurpose(
        key="ad_image",
        bucket_type="public",
        prefix="ads/images",
        visibility="Public",
        allowed_content_types=frozenset(IMAGE_TYPES),
        max_size_bytes=10 * 1024 * 1024,
    ),
    "ad_video": MediaPurpose(
        key="ad_video",
        bucket_type="public",
        prefix="ads/videos",
        visibility="Public",
        allowed_content_types=frozenset(VIDEO_TYPES),
        max_size_bytes=200 * 1024 * 1024,
    ),

    # Public user-generated media
    "review_image": MediaPurpose(
        key="review_image",
        bucket_type="public",
        prefix="reviews/images",
        visibility="Public",
        allowed_content_types=frozenset(IMAGE_TYPES),
        max_size_bytes=10 * 1024 * 1024,
    ),
    "seller_banner": MediaPurpose(
        key="seller_banner",
        bucket_type="public",
        prefix="sellers/banners",
        visibility="Public",
        allowed_content_types=frozenset(IMAGE_TYPES),
        max_size_bytes=10 * 1024 * 1024,
    ),
    "live_cover": MediaPurpose(
        key="live_cover",
        bucket_type="public",
        prefix="live/covers",
        visibility="Public",
        allowed_content_types=frozenset(IMAGE_TYPES),
        max_size_bytes=10 * 1024 * 1024,
    ),
    "profile_image": MediaPurpose(
        key="profile_image",
        bucket_type="public",
        prefix="profiles/images",
        visibility="Public",
        allowed_content_types=frozenset(IMAGE_TYPES),
        max_size_bytes=5 * 1024 * 1024,
    ),
    "category_icon": MediaPurpose(
        key="category_icon",
        bucket_type="public",
        prefix="catalog/categories",
        visibility="Public",
        allowed_content_types=frozenset({*IMAGE_TYPES, "image/svg+xml"}),
        max_size_bytes=5 * 1024 * 1024,
    ),

    # Private or restricted media
    "chat_attachment": MediaPurpose(
        key="chat_attachment",
        bucket_type="private",
        prefix="chat/attachments",
        visibility="Private",
        allowed_content_types=frozenset({*IMAGE_TYPES, *VIDEO_TYPES, *AUDIO_TYPES, *DOCUMENT_TYPES}),
        max_size_bytes=50 * 1024 * 1024,
    ),
    "verification_document": MediaPurpose(
        key="verification_document",
        bucket_type="private",
        prefix="verification/documents",
        visibility="Private",
        allowed_content_types=frozenset({*IMAGE_TYPES, *DOCUMENT_TYPES}),
        max_size_bytes=20 * 1024 * 1024,
    ),
    "background_removal_source": MediaPurpose(
        key="background_removal_source",
        bucket_type="private",
        prefix="background-removal/originals",
        visibility="Private",
        allowed_content_types=frozenset(IMAGE_TYPES),
        max_size_bytes=10 * 1024 * 1024,
    ),

    # Future Shorts/Sounds normalization. Existing Shorts flow remains unchanged.
    "short_video_raw": MediaPurpose(
        key="short_video_raw",
        bucket_type="private",
        prefix="shorts/raw",
        visibility="Private",
        allowed_content_types=frozenset(VIDEO_TYPES),
        max_size_bytes=300 * 1024 * 1024,
    ),
    "short_thumbnail": MediaPurpose(
        key="short_thumbnail",
        bucket_type="public",
        prefix="shorts/thumbnails",
        visibility="Public",
        allowed_content_types=frozenset(IMAGE_TYPES),
        max_size_bytes=5 * 1024 * 1024,
    ),
    "sound_upload": MediaPurpose(
        key="sound_upload",
        bucket_type="public",
        prefix="sounds/uploads",
        visibility="Public",
        allowed_content_types=frozenset(AUDIO_TYPES),
        max_size_bytes=50 * 1024 * 1024,
    ),
}


def normalize_purpose_key(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def get_media_purpose(value: object) -> MediaPurpose | None:
    key = normalize_purpose_key(value)
    return MEDIA_PURPOSES.get(key)


def list_media_purposes() -> list[str]:
    return sorted(MEDIA_PURPOSES)
