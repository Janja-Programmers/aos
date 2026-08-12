"""Authorization and reportability policy shared by Report workflows."""

from __future__ import annotations

import frappe

from aos.services.accounts.constants import ACCOUNT_STATUS_ACTIVE

from .constants import MODERATOR_ROLES
from .errors import ReportNotFoundError, ReportPermissionError


def is_reviewer(user: str | None) -> bool:
    actor = str(user or "").strip()
    if not actor or actor == "Guest":
        return False
    if actor == "Administrator":
        return True
    return bool(set(frappe.get_roles(actor)).intersection(MODERATOR_ROLES))


def require_reviewer(user: str | None) -> None:
    if not is_reviewer(user):
        raise ReportPermissionError("Moderator permission is required.")


def require_reportable_user(*, target_user: str, reporter: str) -> None:
    if not target_user:
        raise ReportPermissionError("Target user is required.", code="VALIDATION_ERROR", http_status=422)
    if target_user == reporter:
        raise ReportPermissionError("You cannot report yourself.", code="VALIDATION_ERROR", http_status=422)
    user = frappe.db.get_value("User", target_user, ["name", "enabled"], as_dict=True)
    profile = frappe.db.get_value(
        "AOS Profile", {"user": target_user}, ["name", "account_status", "is_deleted"], as_dict=True
    )
    if (
        not user
        or int(user.enabled or 0) != 1
        or not profile
        or str(profile.account_status or ACCOUNT_STATUS_ACTIVE) != ACCOUNT_STATUS_ACTIVE
        or int(profile.is_deleted or 0)
    ):
        raise ReportNotFoundError("User not found.")
