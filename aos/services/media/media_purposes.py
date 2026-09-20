"""Central, immutable purpose policies for every AOS-owned media upload.

The registry is the only place where client-selectable media purposes map to
storage visibility, file policy, lifecycle behavior, and attachment contracts.
Infrastructure values such as bucket names remain environment-driven.
"""

from __future__ import annotations

from dataclasses import dataclass

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
VIDEO_TYPES = frozenset({"video/mp4", "video/quicktime"})
AUDIO_TYPES = frozenset({"audio/mpeg", "audio/mp4", "audio/aac", "audio/wav", "audio/ogg"})
DOCUMENT_TYPES = frozenset({"application/pdf"})

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg"})
DOCUMENT_EXTENSIONS = frozenset({".pdf"})
HLS_TYPES = frozenset({"application/vnd.apple.mpegurl", "application/x-mpegURL"})
HLS_EXTENSIONS = frozenset({".m3u8"})
JSON_TYPES = frozenset({"application/json"})
JSON_EXTENSIONS = frozenset({".json"})


@dataclass(frozen=True)
class MediaPurpose:
    key: str
    bucket_type: str
    prefix: str
    visibility: str
    allowed_content_types: frozenset[str]
    allowed_extensions: frozenset[str]
    max_size_bytes: int
    media_kind: str
    max_items_per_resource: int
    allowed_attachment_doctypes: frozenset[str]
    client_upload_allowed: bool = True
    required_permission_doctype: str | None = None
    required_permission_type: str = "write"
    require_image_dimensions: bool = False
    min_width: int | None = None
    min_height: int | None = None
    max_width: int | None = None
    max_height: int | None = None
    max_duration_seconds: int | None = None
    upload_expiry_minutes: int | None = None
    multipart_threshold_bytes: int | None = None
    multipart_part_size_bytes: int | None = None
    multipart_session_expiry_hours: int = 24
    # Private multipart uploads can safely assemble directly at their canonical
    # object key. This avoids an otherwise redundant full-object server-side
    # copy after completion while retaining the staged-key isolation used by
    # direct presigned PUTs and public media.
    multipart_upload_to_final: bool = False
    processing_required: bool = False
    deletion_permitted: bool = True
    replacement_permitted: bool = True
    orphan_retention_days: int = 7

    @property
    def is_public(self) -> bool:
        return self.visibility == "Public"

    @property
    def is_private(self) -> bool:
        return self.visibility == "Private"


MEDIA_PURPOSES: dict[str, MediaPurpose] = {
    "ad_image": MediaPurpose(
        key="ad_image",
        bucket_type="public",
        prefix="ads/images",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=10 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=4,
        allowed_attachment_doctypes=frozenset({"AOS Ad"}),
        require_image_dimensions=True,
        min_width=64,
        min_height=64,
        max_width=12000,
        max_height=12000,
    ),
    "ad_video": MediaPurpose(
        key="ad_video",
        bucket_type="public",
        prefix="ads/videos",
        visibility="Public",
        allowed_content_types=VIDEO_TYPES,
        allowed_extensions=VIDEO_EXTENSIONS,
        max_size_bytes=200 * 1024 * 1024,
        media_kind="video",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Ad"}),
        max_duration_seconds=300,
    ),
    "review_image": MediaPurpose(
        key="review_image",
        bucket_type="public",
        prefix="reviews/images",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=10 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=5,
        allowed_attachment_doctypes=frozenset({"AOS Review"}),
        require_image_dimensions=True,
        min_width=32,
        min_height=32,
        max_width=12000,
        max_height=12000,
    ),
    "seller_banner": MediaPurpose(
        key="seller_banner",
        bucket_type="public",
        prefix="sellers/banners",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=10 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Seller"}),
        require_image_dimensions=True,
        min_width=320,
        min_height=120,
        max_width=12000,
        max_height=12000,
    ),
    "live_cover": MediaPurpose(
        key="live_cover",
        bucket_type="public",
        prefix="live/covers",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=10 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Live Stream"}),
        require_image_dimensions=True,
        min_width=160,
        min_height=160,
        max_width=12000,
        max_height=12000,
    ),
    "profile_image": MediaPurpose(
        key="profile_image",
        bucket_type="public",
        prefix="profiles/images",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=5 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Profile"}),
        require_image_dimensions=True,
        min_width=64,
        min_height=64,
        max_width=8000,
        max_height=8000,
    ),
    "category_icon": MediaPurpose(
        key="category_icon",
        bucket_type="public",
        prefix="catalog/categories",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=5 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Category"}),
        required_permission_doctype="AOS Category",
        required_permission_type="write",
        require_image_dimensions=True,
        min_width=16,
        min_height=16,
        max_width=4096,
        max_height=4096,
    ),
    "chat_attachment": MediaPurpose(
        key="chat_attachment",
        bucket_type="private",
        prefix="chat/attachments",
        visibility="Private",
        allowed_content_types=frozenset({*IMAGE_TYPES, *VIDEO_TYPES, *AUDIO_TYPES, *DOCUMENT_TYPES}),
        allowed_extensions=frozenset({*IMAGE_EXTENSIONS, *VIDEO_EXTENSIONS, *AUDIO_EXTENSIONS, *DOCUMENT_EXTENSIONS}),
        max_size_bytes=50 * 1024 * 1024,
        media_kind="mixed",
        max_items_per_resource=10,
        allowed_attachment_doctypes=frozenset({"AOS Message"}),
        require_image_dimensions=False,
        replacement_permitted=False,
        orphan_retention_days=3,
    ),
    "verification_document": MediaPurpose(
        key="verification_document",
        bucket_type="private",
        prefix="verification/documents",
        visibility="Private",
        allowed_content_types=frozenset({*IMAGE_TYPES, *DOCUMENT_TYPES}),
        allowed_extensions=frozenset({*IMAGE_EXTENSIONS, *DOCUMENT_EXTENSIONS}),
        max_size_bytes=20 * 1024 * 1024,
        media_kind="document",
        max_items_per_resource=10,
        allowed_attachment_doctypes=frozenset({"AOS Verification Request"}),
        replacement_permitted=True,
        orphan_retention_days=1,
    ),
    "background_removal_source": MediaPurpose(
        key="background_removal_source",
        bucket_type="private",
        prefix="background-removal/originals",
        visibility="Private",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=10 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset(),
        require_image_dimensions=True,
        max_width=12000,
        max_height=12000,
        orphan_retention_days=1,
    ),
    "short_video_raw": MediaPurpose(
        key="short_video_raw",
        bucket_type="private",
        prefix="shorts/raw",
        visibility="Private",
        allowed_content_types=VIDEO_TYPES,
        allowed_extensions=VIDEO_EXTENSIONS,
        max_size_bytes=300 * 1024 * 1024,
        media_kind="video",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        max_duration_seconds=600,
        upload_expiry_minutes=60,
        multipart_threshold_bytes=16 * 1024 * 1024,
        multipart_part_size_bytes=8 * 1024 * 1024,
        multipart_session_expiry_hours=24,
        multipart_upload_to_final=True,
        processing_required=True,
        replacement_permitted=False,
        orphan_retention_days=1,
    ),
    "short_photo": MediaPurpose(
        key="short_photo",
        bucket_type="public",
        prefix="shorts/photos",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=15 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=20,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        require_image_dimensions=True,
        min_width=64,
        min_height=64,
        max_width=12000,
        max_height=12000,
        replacement_permitted=False,
    ),
    "short_video_playback": MediaPurpose(
        key="short_video_playback",
        bucket_type="public",
        prefix="shorts/playback",
        visibility="Public",
        allowed_content_types=frozenset({"video/mp4"}),
        allowed_extensions=frozenset({".mp4"}),
        max_size_bytes=400 * 1024 * 1024,
        media_kind="video",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        client_upload_allowed=False,
        max_duration_seconds=600,
        replacement_permitted=False,
    ),
    "short_video_manifest": MediaPurpose(
        key="short_video_manifest",
        bucket_type="public",
        prefix="shorts/playback",
        visibility="Public",
        allowed_content_types=HLS_TYPES,
        allowed_extensions=HLS_EXTENSIONS,
        max_size_bytes=2 * 1024 * 1024,
        media_kind="video",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        client_upload_allowed=False,
        replacement_permitted=False,
    ),
    "short_poster": MediaPurpose(
        key="short_poster",
        bucket_type="public",
        prefix="shorts/posters",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=5 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        client_upload_allowed=False,
        require_image_dimensions=True,
        max_width=4096,
        max_height=4096,
        replacement_permitted=False,
    ),
    "short_storyboard": MediaPurpose(
        key="short_storyboard",
        bucket_type="public",
        prefix="shorts/storyboards",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=12 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        client_upload_allowed=False,
        max_width=16384,
        max_height=16384,
        replacement_permitted=False,
    ),
    "short_storyboard_manifest": MediaPurpose(
        key="short_storyboard_manifest",
        bucket_type="public",
        prefix="shorts/storyboards",
        visibility="Public",
        allowed_content_types=JSON_TYPES,
        allowed_extensions=JSON_EXTENSIONS,
        max_size_bytes=512 * 1024,
        media_kind="document",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        client_upload_allowed=False,
        replacement_permitted=False,
    ),
    "short_download": MediaPurpose(
        key="short_download",
        bucket_type="private",
        prefix="shorts/downloads",
        visibility="Private",
        allowed_content_types=frozenset({"video/mp4"}),
        allowed_extensions=frozenset({".mp4"}),
        max_size_bytes=400 * 1024 * 1024,
        media_kind="video",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        client_upload_allowed=False,
        replacement_permitted=False,
        orphan_retention_days=1,
    ),
    "short_thumbnail": MediaPurpose(
        key="short_thumbnail",
        bucket_type="public",
        prefix="shorts/thumbnails",
        visibility="Public",
        allowed_content_types=IMAGE_TYPES,
        allowed_extensions=IMAGE_EXTENSIONS,
        max_size_bytes=5 * 1024 * 1024,
        media_kind="image",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Short"}),
        client_upload_allowed=False,
        require_image_dimensions=True,
        max_width=4096,
        max_height=4096,
    ),
    "short_original_audio": MediaPurpose(
        key="short_original_audio",
        bucket_type="public",
        prefix="shorts/original-audio",
        visibility="Public",
        allowed_content_types=AUDIO_TYPES,
        allowed_extensions=AUDIO_EXTENSIONS,
        max_size_bytes=80 * 1024 * 1024,
        media_kind="audio",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Sound"}),
        client_upload_allowed=False,
        max_duration_seconds=600,
        replacement_permitted=False,
    ),
    "sound_upload": MediaPurpose(
        key="sound_upload",
        bucket_type="public",
        prefix="sounds/uploads",
        visibility="Public",
        allowed_content_types=AUDIO_TYPES,
        allowed_extensions=AUDIO_EXTENSIONS,
        max_size_bytes=50 * 1024 * 1024,
        media_kind="audio",
        max_items_per_resource=1,
        allowed_attachment_doctypes=frozenset({"AOS Sound"}),
        max_duration_seconds=600,
        replacement_permitted=False,
    ),
}


def normalize_purpose_key(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def get_media_purpose(value: object) -> MediaPurpose | None:
    return MEDIA_PURPOSES.get(normalize_purpose_key(value))


def list_media_purposes(*, client_upload_only: bool = False) -> list[str]:
    return sorted(
        key
        for key, policy in MEDIA_PURPOSES.items()
        if not client_upload_only or policy.client_upload_allowed
    )
