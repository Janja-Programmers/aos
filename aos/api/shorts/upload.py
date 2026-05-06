"""
Upload APIs for Shorts.

Handles:
- init upload (presigned URL)
- confirm upload (trigger processing)
- update metadata (caption, hashtags, content mode)
"""

from __future__ import annotations

import json
import frappe

from aos.services.minio_service import MinioService

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

from aos.api.shorts.constants import (
    INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
    CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
    SHORT_CONTENT_MODE_SHOP,
)


def _get_seller_for_user(user: str) -> str | None:
    """
    Resolve seller profile for current phase.

    Current product rule:
    - Only sellers can access create-short UI.
    - Backend still resolves seller defensively.
    """
    if not user:
        return None

    if frappe.db.exists("AOS Seller", user):
        return user

    return frappe.db.get_value("AOS Seller", {"user": user}, "name")


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

        doc = frappe.get_doc({
            "doctype": "AOS Short",
            "file_key": file_key,
            "status": "initialized",
            "owner": user,
        })
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

        # enqueue processing
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

        seller = _get_seller_for_user(user)
        if not seller:
            return fail(
                "Seller profile is required to publish shorts.",
                code="SELLER_REQUIRED",
            )

        doc.content_mode = content_mode
        doc.seller = seller
        doc.caption = caption
        doc.hashtags = json.dumps(hashtags or [])

        if content_mode == SHORT_CONTENT_MODE_SHOP:
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

            doc.ad = ad.name
            doc.country = getattr(ad, "country", None)

        else:
            doc.ad = None

        doc.visibility_status = "visible"
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Short published successfully.",
            data={
                "short_id": doc.name,
                "content_mode": doc.content_mode,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "update_short_metadata failed")
        frappe.db.rollback()
        return fail("Failed to update short", code="INTERNAL_ERROR")
