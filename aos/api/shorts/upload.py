"""
Short creation and metadata APIs.

Uploads are now generic-only through ``aos.api.v1.media.init_upload`` and
``aos.api.v1.media.confirm_upload``. This module accepts uploaded media IDs and
creates the Short business record / queues processing.
"""

from __future__ import annotations

import json
import frappe

from aos.services.video_processing_service import create_video_processing_job
from aos.services.shorts.classification import (
    apply_publish_result,
    classify_for_publish,
    public_classification,
)
from aos.services.moderation_service import enqueue_short_moderation

from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id

from aos.api.shorts.validators import (
    validate_caption,
    normalize_hashtags,
)

from aos.api.shorts.mentions import sync_short_mentions
from aos.api.shorts.sounds import (
    set_short_sound,
    validate_existing_short_sound_for_mode,
    enqueue_short_audio_reprocess,
)

from aos.api.shorts.constants import (
    CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
    SHORT_CONTENT_MODE_SHOP,
    DEFAULT_SHORT_AUDIENCE,
    VALID_SHORT_AUDIENCES,
    DEFAULT_ALLOW_COMMENTS,
    DEFAULT_ALLOW_DOWNLOADS,
)


def _get_seller_for_user(user: str) -> str | None:
    """
    Resolve seller profile for the given user.

    Product rule:
    - Any logged-in user can publish non-shop shorts.
    - Shop shorts require a seller profile and an active ad.
    """
    if not user:
        return None

    # Some installs may use seller docname == user.
    if frappe.db.exists("AOS Seller", user):
        return user

    return frappe.db.get_value("AOS Seller", {"user": user}, "name")


def _normalize_audience(value) -> tuple[str | None, object | None]:
    """
    Normalize and validate short audience.

    Supported values:
    - everyone
    - followers
    - friends
    - only_me

    Missing audience defaults to everyone to preserve existing behavior.
    """
    audience = value or DEFAULT_SHORT_AUDIENCE
    audience = str(audience).strip().lower()

    if audience not in VALID_SHORT_AUDIENCES:
        return None, fail("Invalid audience.", error="VALIDATION_ERROR")

    return audience, None


def _normalize_bool(value, *, default: int = 1) -> int:
    """
    Normalize flexible boolean input into 1 or 0.

    Accepts:
    - true/false
    - 1/0
    - "true"/"false"
    - "yes"/"no"
    - "on"/"off"

    Invalid or missing values fall back to default.
    """
    if value is None:
        return 1 if default else 0

    if isinstance(value, bool):
        return 1 if value else 0

    if isinstance(value, int):
        return 1 if value else 0

    value = str(value).strip().lower()

    if value in {"1", "true", "yes", "y", "on"}:
        return 1

    if value in {"0", "false", "no", "n", "off"}:
        return 0

    return 1 if default else 0


def _media_error_response(exc: Exception):
    """Convert media service exceptions to stable API responses."""
    if isinstance(exc, MediaNotFoundError):
        return safe_fail_from_exception(exc, fallback="Media not found.", error="NOT_FOUND")
    if isinstance(exc, MediaPermissionError):
        return safe_fail_from_exception(exc, fallback="Not allowed.", error="FORBIDDEN")
    if isinstance(exc, MediaValidationError):
        return safe_fail_from_exception(exc, fallback="Invalid media.", error="VALIDATION_ERROR")
    return None



# CREATE SHORT
def create_short_impl(**kwargs):
    """Create a short from an uploaded raw-video media object.

    Required client flow:
    1. aos.api.v1.media.init_upload with purpose=short_video_raw
    2. PUT video to the returned upload_url
    3. aos.api.v1.media.confirm_upload with media_id
    4. aos.api.v1.shorts.create_short with raw_video_media/media_id

    This replaces the old shorts-specific init_upload/confirm_upload endpoints.
    """
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:create:user:{user}",
        ttl_seconds=60,
        limit=CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    raw_video_media, err = require_id(
        kwargs.get("raw_video_media")
        or kwargs.get("media_id")
        or kwargs.get("media"),
        "raw_video_media",
    )
    if err:
        return err

    audience, err = _normalize_audience(kwargs.get("audience"))
    if err:
        return err

    allow_comments = _normalize_bool(
        kwargs.get("allow_comments"),
        default=DEFAULT_ALLOW_COMMENTS,
    )
    allow_downloads = _normalize_bool(
        kwargs.get("allow_downloads"),
        default=DEFAULT_ALLOW_DOWNLOADS,
    )

    try:
        media_service = MediaService()

        try:
            media_doc = media_service.confirm_upload(
                user=user,
                media_id=raw_video_media,
            )
        except (MediaValidationError, MediaPermissionError, MediaNotFoundError) as exc:
            media_err = _media_error_response(exc)
            if media_err:
                return media_err
            return fail("Upload not completed or file missing.", error="FILE_MISSING")

        if media_doc.purpose != "short_video_raw":
            return fail("Raw short video media has the wrong purpose.", error="VALIDATION_ERROR")

        if media_doc.visibility != "Private":
            return fail("Raw short video media must be private.", error="VALIDATION_ERROR")

        if media_doc.status != "Uploaded":
            return fail("Raw short video media must be uploaded before creating a short.", error="VALIDATION_ERROR")

        doc = frappe.get_doc(
            {
                "doctype": "AOS Short",
                "file_key": media_doc.object_key,
                "raw_video_media": media_doc.name,
                "status": "uploaded",
                "owner": user,
                "audience": audience,
                "allow_comments": allow_comments,
                "allow_downloads": allow_downloads,
            }
        )
        doc.insert(ignore_permissions=True)

        media_service.attach_media(
            media_id=media_doc.name,
            user=user,
            purpose="short_video_raw",
            attached_doctype="AOS Short",
            attached_name=doc.name,
            attached_field="raw_video_media",
        )

        video_job = create_video_processing_job(
            short_id=doc.name,
            force=False,
            reason="short_upload",
            enqueue=True,
        )


        return ok(
            "Short created and queued for processing.",
            data={
                "short_id": doc.name,
                "media_id": media_doc.name,
                "raw_video_media": media_doc.name,
                "status": frappe.db.get_value("AOS Short", doc.name, "status"),
                "classification": public_classification(doc),
                "video_job_id": video_job.name,
                "video_job_status": video_job.status,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")
    except Exception:
        frappe.log_error("Shorts operation failed.", "create_short failed")
        return fail("Failed to create short.", error="INTERNAL_ERROR")


# UPDATE METADATA
def update_short_metadata_impl(**kwargs):
    """Publish/update metadata with server-owned automatic content classification.

    ``content_mode`` remains an accepted legacy transport field so older clients
    do not break, but it is never authoritative.  A validated active ``ad_id``
    is trusted commerce context and classifies the Short as ``shop``; otherwise
    visual processing evidence is fused with caption and hashtag signals.
    """
    user, err = require_login()
    if err:
        return err

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    caption, err = validate_caption(kwargs.get("caption"))
    if err:
        return err

    audience, err = _normalize_audience(kwargs.get("audience"))
    if err:
        return err

    allow_comments = _normalize_bool(
        kwargs.get("allow_comments"),
        default=DEFAULT_ALLOW_COMMENTS,
    )
    allow_downloads = _normalize_bool(
        kwargs.get("allow_downloads"),
        default=DEFAULT_ALLOW_DOWNLOADS,
    )
    hashtags = normalize_hashtags(kwargs.get("hashtags"))

    try:
        doc = frappe.get_doc("AOS Short", short_id)

        if doc.owner != user:
            return fail("Not allowed.", error="FORBIDDEN")

        if doc.status != "ready":
            return fail("Short not ready for publishing.", error="VALIDATION_ERROR")

        was_visible = doc.visibility_status == "visible"
        ad_field_supplied = "ad_id" in kwargs
        requested_ad_id = str(kwargs.get("ad_id") or "").strip() or None

        # The ad link is the only creator-provided signal allowed to establish
        # Shop mode. It is validated as owned, active commerce context. Omitting
        # ad_id during an edit preserves an existing valid attachment; supplying
        # an empty ad_id explicitly detaches it.
        commerce_ad_id = requested_ad_id
        if not ad_field_supplied and getattr(doc, "ad", None):
            commerce_ad_id = str(doc.ad).strip() or None

        if commerce_ad_id:
            ad_id, err = require_id(commerce_ad_id, "ad_id")
            if err:
                return err

            seller = _get_seller_for_user(user)
            if not seller:
                return fail(
                    "Seller profile is required to attach a product to a Short.",
                    error="SELLER_REQUIRED",
                )

            ad = frappe.get_doc("AOS Ad", ad_id)
            if ad.status != "Active":
                return fail(
                    "Shorts can only be attached to active ads.",
                    error="VALIDATION_ERROR",
                )
            if ad.seller != seller:
                return fail("Not allowed to attach to this ad.", error="FORBIDDEN")

            doc.seller = seller
            doc.ad = ad.name
            doc.country = getattr(ad, "country", None)
        else:
            doc.seller = None
            doc.ad = None
            doc.country = None

        classification = classify_for_publish(
            visual_scores=getattr(doc, "classification_visual_scores", None),
            caption=caption,
            hashtags=hashtags,
            has_shop_context=bool(doc.ad),
            legacy_content_mode=kwargs.get("content_mode"),
        )
        apply_publish_result(doc, classification)

        doc.audience = audience
        doc.allow_comments = allow_comments
        doc.allow_downloads = allow_downloads
        doc.caption = caption
        doc.hashtags = json.dumps(hashtags or [])

        sound = None
        sound_id = kwargs.get("sound_id")
        if doc.content_mode == SHORT_CONTENT_MODE_SHOP and not sound_id:
            existing_sound_err = validate_existing_short_sound_for_mode(
                short_id=doc.name,
                content_mode=doc.content_mode,
            )
            if existing_sound_err:
                return existing_sound_err

        doc.visibility_status = "hidden"
        doc.approval_status = "pending"
        doc.hidden_reason = "Pending content moderation"
        doc.save(ignore_permissions=True)

        if sound_id:
            sound = set_short_sound(
                short_id=doc.name,
                sound_id=sound_id,
                start_ms=kwargs.get("sound_start_ms") or kwargs.get("start_ms"),
                duration_ms=kwargs.get("sound_duration_ms") or kwargs.get("duration_ms"),
                volume=(
                    kwargs.get("sound_volume")
                    if kwargs.get("sound_volume") is not None
                    else kwargs.get("volume")
                ),
            )

        mentions = sync_short_mentions(
            short_id=doc.name,
            text=caption,
            mentioned_by=user,
        )

        moderation_job = enqueue_short_moderation(
            doc.name,
            source="short_publish",
            was_visible=was_visible,
        )

        if sound_id:
            enqueue_short_audio_reprocess(doc.name)

        return ok(
            "Short classified and queued for moderation.",
            data={
                "short_id": doc.name,
                "content_mode": doc.content_mode,
                "classification": public_classification(doc),
                "audience": doc.audience,
                "allow_comments": bool(int(doc.allow_comments or 0)),
                "allow_downloads": bool(int(doc.allow_downloads or 0)),
                "mentions": mentions,
                "sound": sound,
                "audio_mix_status": (
                    "pending" if sound_id else getattr(doc, "audio_mix_status", None)
                ),
                "visibility_status": doc.visibility_status,
                "approval_status": getattr(doc, "approval_status", None),
                "moderation_job_id": getattr(moderation_job, "name", None),
                "moderation_job_status": getattr(moderation_job, "status", None),
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")
    except Exception:
        frappe.log_error("Shorts operation failed.", "update_short_metadata failed")
        return fail("Failed to update short", error="INTERNAL_ERROR")
