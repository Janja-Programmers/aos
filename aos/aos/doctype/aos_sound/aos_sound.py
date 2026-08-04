# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

from aos.api.shorts.constants import (
    DEFAULT_SOUND_SOURCE_TYPE,
    DEFAULT_SOUND_STATUS,
    MAX_SOUND_DURATION_SECONDS,
    SOUND_SOURCE_TYPE_COMMERCIAL,
    VALID_SOUND_SOURCE_TYPES,
    VALID_SOUND_STATUSES,
)
from aos.services.media.media_service import MediaError, MediaService

SOUND_MEDIA_PURPOSE = "sound_upload"
SOUND_MEDIA_FIELD = "sound_media"
SOUND_URL_FIELD = "file_url"
SOUND_KEY_FIELD = "file_key"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_media_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("media_id") or value.get("id") or value.get("name")
    return _clean(value)


def _media_actor(doc: Document) -> str:
    session_user = _clean(getattr(frappe.session, "user", ""))
    if session_user and session_user != "Guest":
        return session_user
    return _clean(getattr(doc, "owner", "")) or "Administrator"


class AOSSound(Document):
    """Reusable Shorts sound backed by one immutable AOS Media object."""

    def before_insert(self):
        self._set_defaults()

    def validate(self):
        self._set_defaults()
        self._validate_title()
        self._validate_source_type()
        self._validate_status()
        self._sync_sound_media()
        self._validate_duration()
        self._normalize_flags()

    def on_update(self):
        self._finalize_sound_media_relationship()

    def on_trash(self):
        media_id = _normalize_media_id(getattr(self, SOUND_MEDIA_FIELD, None))
        if not media_id:
            return
        MediaService().release_media(
            media_id=media_id,
            user=_media_actor(self),
            attached_doctype=self.doctype,
            attached_name=self.name,
            replacement_media_id=None,
        )

    def _set_defaults(self):
        if not self.status:
            self.status = DEFAULT_SOUND_STATUS

        if not self.source_type:
            self.source_type = DEFAULT_SOUND_SOURCE_TYPE

        if not self.owner:
            self.owner = frappe.session.user if frappe.session.user != "Guest" else None

        if self.usage_count in (None, ""):
            self.usage_count = 0

        if getattr(self, "favorite_count", None) in (None, ""):
            self.favorite_count = 0

    def _validate_title(self):
        if not self.title:
            frappe.throw(_("Sound title is required"))

        self.title = str(self.title).strip()
        if not self.title:
            frappe.throw(_("Sound title is required"))

    def _validate_source_type(self):
        self.source_type = str(self.source_type or DEFAULT_SOUND_SOURCE_TYPE).strip().lower()
        if self.source_type not in VALID_SOUND_SOURCE_TYPES:
            frappe.throw(_("Invalid sound source type"))

    def _validate_status(self):
        self.status = str(self.status or DEFAULT_SOUND_STATUS).strip().lower()
        if self.status not in VALID_SOUND_STATUSES:
            frappe.throw(_("Invalid sound status"))

    def _sync_sound_media(self) -> None:
        previous = self.get_doc_before_save()
        previous_media_id = _normalize_media_id(
            getattr(previous, SOUND_MEDIA_FIELD, None) if previous else None
        )
        self._previous_sound_media_id = previous_media_id

        media_id = _normalize_media_id(getattr(self, SOUND_MEDIA_FIELD, None))
        if not media_id:
            frappe.throw(_("Sound media is required"))

        if previous_media_id and media_id != previous_media_id:
            frappe.throw(
                _("Audio cannot be replaced on an existing sound. Create a new sound instead.")
            )

        actor = _media_actor(self)
        try:
            service = MediaService()
            media = service.validate_media_for_use(
                media_id=media_id,
                user=actor,
                purpose=SOUND_MEDIA_PURPOSE,
                attached_doctype=self.doctype if not self.is_new() else None,
                attached_name=self.name if not self.is_new() else None,
            )
        except MediaError as exc:
            frappe.throw(_(str(exc) or "Invalid sound media."))

        setattr(self, SOUND_KEY_FIELD, _clean(getattr(media, "object_key", "")))
        setattr(self, SOUND_URL_FIELD, service.get_public_url(media.name))

        media_duration = getattr(media, "duration_seconds", None)
        if self.duration_seconds in (None, "", 0, 0.0) and media_duration:
            self.duration_seconds = float(media_duration)

    def _finalize_sound_media_relationship(self) -> None:
        media_id = _normalize_media_id(getattr(self, SOUND_MEDIA_FIELD, None))
        if not media_id:
            return

        MediaService().attach_media(
            media_id=media_id,
            user=_media_actor(self),
            purpose=SOUND_MEDIA_PURPOSE,
            attached_doctype=self.doctype,
            attached_name=self.name,
            attached_field=SOUND_MEDIA_FIELD,
        )

    def _validate_duration(self):
        if self.duration_seconds in (None, ""):
            self.duration_seconds = 0
            return

        try:
            self.duration_seconds = float(self.duration_seconds or 0)
        except (TypeError, ValueError):
            frappe.throw(_("Invalid sound duration"))

        if self.duration_seconds < 0:
            frappe.throw(_("Invalid sound duration"))

        if self.duration_seconds > MAX_SOUND_DURATION_SECONDS:
            frappe.throw(
                _("Sound duration cannot exceed {0} seconds").format(
                    MAX_SOUND_DURATION_SECONDS
                )
            )

    def _normalize_flags(self):
        if self.source_type == SOUND_SOURCE_TYPE_COMMERCIAL:
            self.is_commercial_safe = 1
        else:
            self.is_commercial_safe = 1 if int(self.is_commercial_safe or 0) else 0
