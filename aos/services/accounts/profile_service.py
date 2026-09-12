"""Accounts profile application service."""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.social.capabilities import SocialCapabilityService
from aos.services.media.media_service import (
    MediaConflictError,
    MediaError,
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaStorageError,
)

from .constants import ACCOUNT_STATUS_ACTIVE, PROFILE_DOCTYPE, PROFILE_IMAGE_FIELD, PROFILE_IMAGE_PURPOSE
from .errors import AccountError, AccountNotFoundError, AccountValidationError
from .identity import normalize_public_account_id
from .observability import account_log
from .repository import AccountRepository
from .serializers import serialize_private_profile_row, serialize_public_profile_row
from .validation import validate_profile_patch


class AccountProfileService:
    def __init__(self, media: MediaService | None = None, repository: AccountRepository | None = None):
        self.media = media or MediaService()
        self.repository = repository or AccountRepository()

    def get_private_profile(self, *, user: str) -> dict[str, Any]:
        row = self.repository.account_by_user(user)
        if not row:
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")
        return serialize_private_profile_row(row)

    def get_public_profile(self, *, reference: Any, viewer: str | None = None) -> dict[str, Any]:
        account_id = normalize_public_account_id(reference)
        if not account_id:
            raise AccountValidationError("Invalid account id.", code="INVALID_ACCOUNT_ID")
        row = self.repository.account_by_public_id(account_id)
        if not row or int(row.enabled or 0) != 1 or str(row.account_status or "") != ACCOUNT_STATUS_ACTIVE:
            raise AccountNotFoundError("Account not found.")

        relationship = None
        target_user = str(row.user)
        if viewer:
            relationship = SocialCapabilityService().relationship_projection(
                viewer=viewer,
                target=target_user,
                target_account_id=account_id,
            )
            if relationship.get("is_blocked_by_me") or relationship.get("has_blocked_me"):
                raise AccountError("Profile is unavailable.", code="PROFILE_UNAVAILABLE", http_status=403)
        return serialize_public_profile_row(row, relationship=relationship)

    def update_profile(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        changes = validate_profile_patch(payload)
        profile = self.repository.lock_profile(user)
        if not profile:
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")

        user_doc = None
        previous_media_id = str(getattr(profile, PROFILE_IMAGE_FIELD, "") or "")
        changed_fields: list[str] = []

        if "avatar_media_id" in changes:
            media_id = changes.pop("avatar_media_id")
            try:
                # Accounts requires strict ownership for profile media. Do not
                # fall through to Media's broader administrative permission
                # checks when the referenced object belongs to another user.
                # Besides making the Accounts contract explicit, this keeps the
                # normal cross-user rejection path independent of Frappe's
                # generic permission engine.
                candidate_media = self.media.get_media_doc(media_id)
                if str(getattr(candidate_media, "owner_user", "") or "").strip() != str(user or "").strip():
                    raise MediaPermissionError(
                        "Media is not owned by the authenticated account",
                        code="MEDIA_ACCESS_DENIED",
                    )

                media_doc = self.media.attach_media(
                    media_id=media_id,
                    user=user,
                    purpose=PROFILE_IMAGE_PURPOSE,
                    attached_doctype=PROFILE_DOCTYPE,
                    attached_name=profile.name,
                    attached_field=PROFILE_IMAGE_FIELD,
                    replacing_media_id=previous_media_id or None,
                )
            except MediaPermissionError as exc:
                # Accounts intentionally collapses Media ownership/access distinctions
                # at its public boundary. Clients only need to know that this avatar
                # reference is not accessible to the authenticated account; exposing
                # the Media subsystem's finer ownership code adds no useful contract
                # and can reveal authorization-state detail.
                raise AccountError(
                    "Avatar media is not available to this account.",
                    code="MEDIA_ACCESS_DENIED",
                    http_status=403,
                ) from exc
            except MediaNotFoundError as exc:
                raise AccountValidationError("Avatar media was not found.", code=exc.code, http_status=404) from exc
            except MediaConflictError as exc:
                raise AccountError(
                    "Avatar media cannot be attached in its current state.", code=exc.code, http_status=409
                ) from exc
            except MediaStorageError as exc:
                raise AccountError("Avatar service is temporarily unavailable.", code=exc.code, http_status=503) from exc
            except MediaError as exc:
                raise AccountValidationError(
                    "Invalid avatar media.", code=getattr(exc, "code", "INVALID_AVATAR_MEDIA")
                ) from exc

            if previous_media_id != media_doc.name:
                profile.set(PROFILE_IMAGE_FIELD, media_doc.name)
                user_doc = user_doc or frappe.get_doc("User", user)
                user_doc.user_image = self.media.get_public_url(media_doc.name)
                if previous_media_id:
                    self._release_previous_avatar(
                        media_id=previous_media_id,
                        user=user,
                        profile_name=profile.name,
                        replacement_media_id=media_doc.name,
                    )
                changed_fields.append("avatar")

        if changes.pop("remove_avatar", False) and previous_media_id:
            profile.set(PROFILE_IMAGE_FIELD, "")
            user_doc = user_doc or frappe.get_doc("User", user)
            user_doc.user_image = ""
            self._release_previous_avatar(
                media_id=previous_media_id,
                user=user,
                profile_name=profile.name,
            )
            changed_fields.append("avatar")

        for field, value in changes.items():
            if getattr(profile, field, None) == value:
                continue
            profile.set(field, value)
            changed_fields.append(field)
            if field == "display_name":
                user_doc = user_doc or frappe.get_doc("User", user)
                user_doc.first_name = value
                user_doc.full_name = value

        if changed_fields:
            profile.save(ignore_permissions=True)
            if user_doc is not None:
                user_doc.save(ignore_permissions=True)
            account_log("account.profile.updated", user=user, changed_fields=changed_fields)

        row = self.repository.account_by_user(user)
        if not row:
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")
        return serialize_private_profile_row(row)


    def _release_previous_avatar(
        self,
        *,
        media_id: str,
        user: str,
        profile_name: str,
        replacement_media_id: str | None = None,
    ) -> None:
        """Release the previous avatar, tolerating only an already-missing row.

        The locked Profile row is authoritative for the replacement mutation. If
        its old Media link is already missing, there is nothing left to release;
        allowing the mutation repairs that stale reference. Every other Media
        authorization/lifecycle error remains transactional and must propagate.
        """
        try:
            self.media.release_media(
                media_id=media_id,
                user=user,
                attached_doctype=PROFILE_DOCTYPE,
                attached_name=profile_name,
                replacement_media_id=replacement_media_id,
            )
        except MediaNotFoundError:
            account_log(
                "account.avatar.previous_media_missing",
                user=user,
                changed_fields={"avatar"},
                failure_category="stale_media_reference",
            )

    def remove_avatar(self, *, user: str) -> dict[str, Any]:
        return self.update_profile(user=user, payload={"remove_avatar": True})
