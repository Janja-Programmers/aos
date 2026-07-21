# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet

from aos.services.media.media_service import (
    MediaError,
    MediaService,
)

CATEGORY_ICON_PURPOSE = "category_icon"
CATEGORY_ICON_MEDIA_FIELD = "icon_media"
CATEGORY_ICON_URL_FIELD = "icon"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_media_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("media_id") or value.get("id") or value.get("name")
    return _clean(value)


def _looks_like_media_id(value: Any) -> bool:
    return _normalize_media_id(value).startswith("MEDIA-")


def _public_media_url(doc) -> str:
    return MediaService().get_public_url(doc.name)


class AOSCategory(NestedSet):
    """Category tree with centrally owned, admin-managed icon media."""

    def validate(self):
        self._sync_icon_media()

    def on_update(self):
        # Preserve NestedSet tree maintenance before finalizing media ownership.
        super().on_update()
        self._finalize_icon_media_relationship()

    def _sync_icon_media(self) -> None:
        if not hasattr(self, CATEGORY_ICON_MEDIA_FIELD):
            return

        previous = self.get_doc_before_save()
        self._previous_icon_media_id = _clean(
            getattr(previous, CATEGORY_ICON_MEDIA_FIELD, "") if previous else ""
        )
        previous_url = _clean(
            getattr(previous, CATEGORY_ICON_URL_FIELD, "") if previous else ""
        )

        media_id = _normalize_media_id(getattr(self, CATEGORY_ICON_MEDIA_FIELD, None))
        icon_value = _clean(getattr(self, CATEGORY_ICON_URL_FIELD, None))
        if not media_id and _looks_like_media_id(icon_value):
            media_id = _normalize_media_id(icon_value)
            setattr(self, CATEGORY_ICON_MEDIA_FIELD, media_id)

        if media_id:
            doc = self._validate_icon_media(media_id)
            setattr(self, CATEGORY_ICON_URL_FIELD, _public_media_url(doc))
            return

        if icon_value and icon_value != previous_url:
            frappe.throw(
                _("Category icons must be uploaded through Media and selected by media id.")
            )

    def _validate_icon_media(self, media_id: str):
        try:
            return MediaService().validate_media_for_use(
                media_id=media_id,
                user=frappe.session.user,
                purpose=CATEGORY_ICON_PURPOSE,
                attached_doctype=self.doctype if not self.is_new() else None,
                attached_name=self.name if not self.is_new() else None,
            )
        except MediaError as exc:
            frappe.throw(_(str(exc) or "Invalid category icon media."))

    def _finalize_icon_media_relationship(self) -> None:
        media_id = _normalize_media_id(getattr(self, CATEGORY_ICON_MEDIA_FIELD, None))
        previous_media_id = _clean(getattr(self, "_previous_icon_media_id", ""))
        service = MediaService()

        if media_id:
            service.attach_media(
                media_id=media_id,
                user=frappe.session.user,
                purpose=CATEGORY_ICON_PURPOSE,
                attached_doctype=self.doctype,
                attached_name=self.name,
                attached_field=CATEGORY_ICON_MEDIA_FIELD,
                replacing_media_id=previous_media_id,
            )

        if previous_media_id and previous_media_id != media_id:
            service.release_media(
                media_id=previous_media_id,
                user=frappe.session.user,
                attached_doctype=self.doctype,
                attached_name=self.name,
                replacement_media_id=media_id or None,
            )
