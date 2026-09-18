"""Category-image integration with the canonical Media lifecycle."""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.media.identifiers import normalize_media_id
from aos.services.media.media_service import (
    MediaConflictError,
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
)

from .errors import CatalogValidationError

CATEGORY_IMAGE_PURPOSE = "category_icon"
CATEGORY_IMAGE_MEDIA_FIELD = "image_media"


def normalize_category_image_media_id(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise CatalogValidationError("Invalid category image media.", code="INVALID_CATEGORY_IMAGE")
    media_id = normalize_media_id(value)
    if media_id is None:
        raise CatalogValidationError("Invalid category image media.", code="INVALID_CATEGORY_IMAGE")
    return media_id


def prepare_category_image(doc: Any) -> None:
    previous = doc.get_doc_before_save()
    previous_media_id = normalize_category_image_media_id(
        getattr(previous, CATEGORY_IMAGE_MEDIA_FIELD, "") if previous else ""
    )
    media_id = normalize_category_image_media_id(getattr(doc, CATEGORY_IMAGE_MEDIA_FIELD, ""))
    doc.image_media = media_id or None
    doc._previous_image_media_id = previous_media_id

    if not media_id or media_id == previous_media_id:
        return

    MediaService().validate_media_for_use(
        media_id=media_id,
        user=frappe.session.user,
        purpose=CATEGORY_IMAGE_PURPOSE,
        attached_doctype=doc.doctype if not doc.is_new() else None,
        attached_name=doc.name if not doc.is_new() else None,
    )


def finalize_category_image(doc: Any) -> None:
    media_id = normalize_category_image_media_id(getattr(doc, CATEGORY_IMAGE_MEDIA_FIELD, ""))
    previous_media_id = normalize_category_image_media_id(
        getattr(doc, "_previous_image_media_id", "")
    )
    if media_id == previous_media_id:
        return

    service = MediaService()
    if media_id:
        service.attach_media(
            media_id=media_id,
            user=frappe.session.user,
            purpose=CATEGORY_IMAGE_PURPOSE,
            attached_doctype=doc.doctype,
            attached_name=doc.name,
            attached_field=CATEGORY_IMAGE_MEDIA_FIELD,
            replacing_media_id=previous_media_id or None,
        )

    if previous_media_id:
        _release_stale_tolerant(
            service,
            media_id=previous_media_id,
            attached_doctype=doc.doctype,
            attached_name=doc.name,
            replacement_media_id=media_id or None,
        )


def release_category_image_on_delete(doc: Any) -> None:
    media_id = normalize_category_image_media_id(getattr(doc, CATEGORY_IMAGE_MEDIA_FIELD, ""))
    if not media_id:
        return
    _release_stale_tolerant(
        MediaService(),
        media_id=media_id,
        attached_doctype=doc.doctype,
        attached_name=doc.name,
        replacement_media_id=None,
    )


def _release_stale_tolerant(
    service: MediaService,
    *,
    media_id: str,
    attached_doctype: str,
    attached_name: str,
    replacement_media_id: str | None,
) -> None:
    """Release only the canonical old relationship; stale references are repairable.

    A missing Media row or a Media row attached to a different resource means
    the Category reference was already stale. In that case Catalog must not
    mutate somebody else's Media lifecycle and must not prevent the category
    from being repaired by replacement/removal.
    """

    try:
        service.release_media(
            media_id=media_id,
            user=frappe.session.user,
            attached_doctype=attached_doctype,
            attached_name=attached_name,
            replacement_media_id=replacement_media_id,
            system=True,
        )
    except (MediaNotFoundError, MediaConflictError, MediaPermissionError):
        try:
            frappe.logger("aos.catalog", allow_site=True).warning(
                "catalog_stale_category_image_reference"
            )
        except Exception:
            pass
