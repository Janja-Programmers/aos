"""Authorization and reportability policy shared by Reports workflows."""

from __future__ import annotations

import frappe

from aos.services.accounts.constants import ACCOUNT_STATUS_ACTIVE
from aos.utils.doctype_permissions import has_doctype_permission

from .errors import ReportNotFoundError, ReportPermissionError, ReportSelfError


def is_reviewer(user: str | None, *, doctype: str) -> bool:
    actor = str(user or "").strip()
    clean_doctype = str(doctype or "").strip()
    if not actor or actor == "Guest" or not clean_doctype:
        return False
    return has_doctype_permission(user=actor, doctype=clean_doctype, ptype="write")


def require_reviewer(user: str | None, *, doctype: str) -> None:
    if not is_reviewer(user, doctype=doctype):
        raise ReportPermissionError("Write permission on this Report DocType is required.")


def require_reportable_user(*, target_user: str, reporter: str) -> None:
    if not target_user:
        raise ReportNotFoundError("Account not found.")
    if target_user == reporter:
        raise ReportSelfError("You cannot report yourself.")
    user = frappe.db.get_value("User", target_user, ["name", "enabled"], as_dict=True)
    profile = frappe.db.get_value(
        "AOS Profile", {"user": target_user}, ["name", "account_status"], as_dict=True
    )
    if (
        not user
        or int(user.enabled or 0) != 1
        or not profile
        or str(profile.account_status or ACCOUNT_STATUS_ACTIVE) != ACCOUNT_STATUS_ACTIVE
    ):
        raise ReportNotFoundError("Account not found.")
