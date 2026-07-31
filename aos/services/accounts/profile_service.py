"""Accounts profile application service."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.blocking import get_block_status
from aos.api.social.relationship import build_relationship_status
from aos.services.media.media_service import MediaError, MediaService

from .constants import PROFILE_DOCTYPE, PROFILE_IMAGE_FIELD, PROFILE_IMAGE_PURPOSE
from .errors import AccountNotFoundError, AccountPermissionError, AccountValidationError
from .identity import ensure_public_account_id, resolve_account_reference
from .observability import account_log
from .serializers import serialize_private_profile, serialize_public_profile
from .validation import validate_profile_patch


class AccountProfileService:
    def __init__(self, media: MediaService | None = None):
        self.media = media or MediaService()

    def get_private_profile(self, *, user: str) -> dict[str, Any]:
        self._assert_profile_exists(user)
        return serialize_private_profile(user)

    def get_public_profile(self, *, reference: Any, viewer: str | None = None) -> dict[str, Any]:
        user = resolve_account_reference(reference)
        if not user:
            raise AccountNotFoundError("Account not found.")
        self._assert_profile_exists(user)
        self._assert_public_profile_available(user)
        if viewer and viewer != user:
            block = get_block_status(current_user=viewer, target_user=user)
            if block.get("has_blocked_me") or block.get("is_blocked_by_me"):
                raise AccountPermissionError("Profile is unavailable.", code="PROFILE_UNAVAILABLE")
        relationship = build_relationship_status(current_user=viewer, target_user=user) if viewer else None
        return serialize_public_profile(user, relationship=relationship)

    def update_profile(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        changes = validate_profile_patch(payload)
        profile = self._lock_profile(user)
        user_doc = frappe.get_doc("User", user)
        previous_media_id = str(getattr(profile, PROFILE_IMAGE_FIELD, "") or "")
        changed_fields: list[str] = []

        if "avatar_media_id" in changes:
            media_id = changes.pop("avatar_media_id")
            try:
                media_doc = self.media.attach_media(
                    media_id=media_id,
                    user=user,
                    purpose=PROFILE_IMAGE_PURPOSE,
                    attached_doctype=PROFILE_DOCTYPE,
                    attached_name=user,
                    attached_field=PROFILE_IMAGE_FIELD,
                    replacing_media_id=previous_media_id or None,
                )
            except MediaError as exc:
                raise AccountValidationError(str(exc), code=getattr(exc, "code", "INVALID_AVATAR_MEDIA")) from exc
            profile.set(PROFILE_IMAGE_FIELD, media_doc.name)
            user_doc.user_image = self.media.get_public_url(media_doc.name)
            if previous_media_id and previous_media_id != media_doc.name:
                self.media.release_media(
                    media_id=previous_media_id,
                    user=user,
                    attached_doctype=PROFILE_DOCTYPE,
                    attached_name=user,
                    replacement_media_id=media_doc.name,
                )
            changed_fields.append("avatar")

        if changes.pop("remove_avatar", False):
            profile.set(PROFILE_IMAGE_FIELD, "")
            user_doc.user_image = ""
            if previous_media_id:
                self.media.release_media(
                    media_id=previous_media_id,
                    user=user,
                    attached_doctype=PROFILE_DOCTYPE,
                    attached_name=user,
                )
            changed_fields.append("avatar")

        for field, value in changes.items():
            profile.set(field, value)
            changed_fields.append(field)
            if field == "display_name":
                user_doc.first_name = value
                user_doc.full_name = value
            elif field == "bio" and hasattr(user_doc, "bio"):
                user_doc.bio = value
            elif field == "phone":
                if hasattr(user_doc, "mobile_no"):
                    user_doc.mobile_no = value
                if hasattr(user_doc, "phone"):
                    user_doc.phone = value
            elif field == "date_of_birth" and hasattr(user_doc, "birth_date"):
                user_doc.birth_date = value
            elif field == "gender" and hasattr(user_doc, "gender"):
                user_doc.gender = value
            elif field == "location" and hasattr(user_doc, "location"):
                user_doc.location = value

        ensure_public_account_id(profile)
        profile.save(ignore_permissions=True)
        user_doc.save(ignore_permissions=True)
        account_log("account.profile.updated", user=user, changed_fields=changed_fields)
        return serialize_private_profile(user)

    def remove_avatar(self, *, user: str) -> dict[str, Any]:
        return self.update_profile(user=user, payload={"remove_avatar": True})

    @staticmethod
    def _assert_public_profile_available(user: str) -> None:
        row = frappe.db.get_value(
            "AOS Profile",
            user,
            ["account_status", "is_deleted"],
            as_dict=True,
        ) or {}
        enabled = frappe.db.get_value("User", user, "enabled")
        status = str(row.get("account_status") or "Active")
        if int(enabled or 0) != 1 or status != "Active" or int(row.get("is_deleted") or 0):
            raise AccountNotFoundError("Account not found.")

    @staticmethod
    def _assert_profile_exists(user: str) -> None:
        if not user or not frappe.db.exists("User", user):
            raise AccountNotFoundError("Account not found.")
        if not frappe.db.exists(PROFILE_DOCTYPE, user):
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")

    @staticmethod
    def _lock_profile(user: str):
        AccountProfileService._assert_profile_exists(user)
        rows = frappe.db.sql(
            "SELECT name FROM `tabAOS Profile` WHERE user = %s LIMIT 1 FOR UPDATE",
            (user,),
            as_dict=True,
        )
        if not rows:
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")
        return frappe.get_doc(PROFILE_DOCTYPE, rows[0].name)
