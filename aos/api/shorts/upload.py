"""
Upload APIs for Shorts.

Handles:
- init upload (presigned URL)
- confirm upload (trigger processing)
- update metadata (caption, hashtags, content mode, audience, comment/download controls)
"""

from __future__ import annotations

import json
import frappe

from aos.services.minio_service import MinioService
from aos.services.notification_service import NotificationService

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.api.shorts.validators import (
    validate_filename,
    validate_caption,
    normalize_hashtags,
    validate_content_mode,
)

from aos.api.shorts.mentions import sync_short_mentions
from aos.api.shorts.sounds import (
    set_short_sound,
    validate_existing_short_sound_for_mode,
    enqueue_short_audio_reprocess,
)

from aos.api.shorts.constants import (
    INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
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
        return None, fail("Invalid audience.", code="VALIDATION_ERROR")

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


# INIT UPLOAD
def init_upload_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:init:user:{user}",
        ttl_seconds=60,
        limit=INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    filename = kwargs.get("filename")

    ext, err = validate_filename(filename)
    if err:
        return err

    try:
        service = MinioService()

        file_key = service.generate_file_key("raw", filename)
        upload_url = service.get_presigned_upload_url(file_key)
        public_url = service.get_public_url(file_key)

        doc = frappe.get_doc(
            {
                "doctype": "AOS Short",
                "file_key": file_key,
                "status": "initialized",
                "owner": user,
                "audience": DEFAULT_SHORT_AUDIENCE,
                "allow_comments": DEFAULT_ALLOW_COMMENTS,
                "allow_downloads": DEFAULT_ALLOW_DOWNLOADS,
            }
        )
        doc.insert(ignore_permissions=True)

        return ok(
            "Upload initialized.",
            data={
                "short_id": doc.name,
                "file_key": file_key,
                "upload_url": upload_url,
                "upload_headers": {},
                "public_url": public_url,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "init_upload failed")
        return fail("Failed to initialize upload", code="INTERNAL_ERROR")


# CONFIRM UPLOAD
def confirm_upload_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:confirm:user:{user}",
        ttl_seconds=60,
        limit=CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        doc = frappe.get_doc("AOS Short", short_id)

        if doc.owner != user:
            return fail("Not allowed.", code="FORBIDDEN")

        if doc.status != "initialized":
            return fail(
                f"Invalid state: {doc.status}",
                code="VALIDATION_ERROR",
            )

        # Verify file exists in MinIO
        minio = MinioService()
        if not minio.file_exists(doc.file_key):
            return fail(
                "Upload not completed or file missing.",
                code="FILE_MISSING",
            )

        # Move to uploaded
        doc.status = "uploaded"
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        # Enqueue processing
        frappe.enqueue(
            "aos.api.shorts.tasks.process_short_task",
            short_id=doc.name,
            queue="long",
            timeout=1800,
        )

        return ok(
            "Upload confirmed.",
            data={"short_id": doc.name},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "confirm_upload failed")
        frappe.db.rollback()
        return fail("Failed to confirm upload", code="INTERNAL_ERROR")


# UPDATE METADATA
def update_short_metadata_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    content_mode, err = validate_content_mode(kwargs.get("content_mode"))
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
            return fail("Not allowed.", code="FORBIDDEN")

        # Only allow publishing when processing is complete
        if doc.status != "ready":
            return fail(
                "Short not ready for publishing.",
                code="VALIDATION_ERROR",
            )

        was_visible = doc.visibility_status == "visible"

        doc.content_mode = content_mode
        doc.audience = audience
        doc.allow_comments = allow_comments
        doc.allow_downloads = allow_downloads
        doc.caption = caption
        doc.hashtags = json.dumps(hashtags or [])

        if content_mode == SHORT_CONTENT_MODE_SHOP:
            seller = _get_seller_for_user(user)
            if not seller:
                return fail(
                    "Seller profile is required to publish shop shorts.",
                    code="SELLER_REQUIRED",
                )

            ad_id, err = require_id(kwargs.get("ad_id"), "ad_id")
            if err:
                return err

            ad = frappe.get_doc("AOS Ad", ad_id)

            if ad.status != "Active":
                return fail(
                    "Shorts can only be attached to active ads.",
                    code="VALIDATION_ERROR",
                )

            if ad.seller != seller:
                return fail(
                    "Not allowed to attach to this ad.",
                    code="FORBIDDEN",
                )

            doc.seller = seller
            doc.ad = ad.name
            doc.country = getattr(ad, "country", None)

        else:
            # Non-shop shorts can be posted by any logged-in user.
            # They are creator/user content, not commerce/ad content.
            doc.seller = None
            doc.ad = None
            doc.country = None

        sound = None
        sound_id = kwargs.get("sound_id")

        if content_mode == SHORT_CONTENT_MODE_SHOP and not sound_id:
            existing_sound_err = validate_existing_short_sound_for_mode(
                short_id=doc.name,
                content_mode=content_mode,
            )
            if existing_sound_err:
                return existing_sound_err

        doc.visibility_status = "visible"
        doc.hidden_reason = None
        doc.save(ignore_permissions=True)

        if sound_id:
            sound = set_short_sound(
                short_id=doc.name,
                sound_id=sound_id,
                start_ms=kwargs.get("sound_start_ms") or kwargs.get("start_ms"),
                duration_ms=kwargs.get("sound_duration_ms") or kwargs.get("duration_ms"),
                volume=kwargs.get("sound_volume") if kwargs.get("sound_volume") is not None else kwargs.get("volume"),
            )

        mentions = sync_short_mentions(
            short_id=doc.name,
            text=caption,
            mentioned_by=user,
        )

        frappe.db.commit()

        if sound_id:
            enqueue_short_audio_reprocess(doc.name)

        # Notify followers only on first publish, not on later metadata edits.
        if not was_visible:
            NotificationService.notify_new_short(
                actor=doc.owner,
                short_id=doc.name,
            )

        return ok(
            "Short published successfully.",
            data={
                "short_id": doc.name,
                "content_mode": doc.content_mode,
                "audience": doc.audience,
                "allow_comments": bool(int(doc.allow_comments or 0)),
                "allow_downloads": bool(int(doc.allow_downloads or 0)),
                "mentions": mentions,
                "sound": sound,
                "audio_mix_status": "pending" if sound_id else getattr(doc, "audio_mix_status", None),
                "visibility_status": doc.visibility_status,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "update_short_metadata failed")
        frappe.db.rollback()
        return fail("Failed to update short", code="INTERNAL_ERROR")
