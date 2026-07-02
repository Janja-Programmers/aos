# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.nestedset import NestedSet

from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
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
    if _clean(getattr(doc, "public_url", None)):
        return _clean(doc.public_url)
    return MediaService().get_url(media_id=doc.name, user=None)


class AOSCategory(NestedSet):
    def validate(self):
        # NestedSet/Document does not define a base validate() method in this
        # Frappe version. Keep category-specific validation here only.
        self._sync_icon_media()

    def _sync_icon_media(self) -> None:
        """Use category_icon media as source of truth and keep icon as URL cache.

        Category icons are public, admin-managed assets. The old icon field is a
        URL cache for catalog serializers; icon_media stores the AOS Media
        Object relationship.
        """
        if not hasattr(self, CATEGORY_ICON_MEDIA_FIELD):
            return

        old_media_id = _clean(
            frappe.db.get_value(self.doctype, self.name, CATEGORY_ICON_MEDIA_FIELD)
            if self.name and not self.is_new()
            else ""
        )

        media_id = _normalize_media_id(getattr(self, CATEGORY_ICON_MEDIA_FIELD, None))

        # Convenience: allow admins to paste MEDIA-... into the visible icon URL
        # field. The controller converts it to icon_media and writes the URL.
        if not media_id and _looks_like_media_id(getattr(self, CATEGORY_ICON_URL_FIELD, None)):
            media_id = _normalize_media_id(getattr(self, CATEGORY_ICON_URL_FIELD, None))
            setattr(self, CATEGORY_ICON_MEDIA_FIELD, media_id)

        if media_id:
            doc = self._validate_icon_media(media_id)
            icon_url = _public_media_url(doc)
            setattr(self, CATEGORY_ICON_URL_FIELD, icon_url)

            if old_media_id and old_media_id != media_id:
                self._mark_previous_icon_delete_pending(old_media_id)

            if doc.status != "Attached":
                doc.status = "Attached"
                doc.attached_doctype = self.doctype
                doc.attached_name = self.name
                doc.attached_field = CATEGORY_ICON_MEDIA_FIELD
                doc.attached_at = now_datetime()
                doc.save(ignore_permissions=True)
            return

        # If icon_media was cleared, keep any manually-entered public URL as-is,
        # but release the previous media relationship so cleanup can remove it.
        if old_media_id:
            self._mark_previous_icon_delete_pending(old_media_id)

    def _validate_icon_media(self, media_id: str):
        service = MediaService()
        user = frappe.session.user

        try:
            doc = service.get_media_doc(media_id)
            service.assert_user_can_manage(doc, user)
        except MediaNotFoundError:
            frappe.throw(_("Category icon media not found."))
        except MediaPermissionError as exc:
            frappe.throw(_(str(exc) or "You cannot manage this category icon media."))
        except MediaValidationError as exc:
            frappe.throw(_(str(exc) or "Invalid category icon media."))

        if doc.status == "Deleted":
            frappe.throw(_("Category icon media not found."))

        if doc.purpose != CATEGORY_ICON_PURPOSE:
            frappe.throw(_("Category icon media has the wrong purpose."))

        if doc.visibility != "Public":
            frappe.throw(_("Category icon media must be public."))

        if doc.status == "Uploaded":
            return doc

        if doc.status == "Attached":
            if doc.attached_doctype == self.doctype and doc.attached_name == self.name:
                return doc

        frappe.throw(_("Category icon media cannot be used in its current state."))

    def _mark_previous_icon_delete_pending(self, media_id: str) -> None:
        try:
            doc = MediaService().get_media_doc(media_id)
            if doc.purpose != CATEGORY_ICON_PURPOSE:
                return
            if doc.attached_doctype != self.doctype or doc.attached_name != self.name:
                return
            if doc.status not in {"Deleted", "Delete Pending"}:
                doc.status = "Delete Pending"
                doc.attached_doctype = ""
                doc.attached_name = ""
                doc.attached_field = ""
                doc.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "AOS Category Icon Media Release Failed",
            )
