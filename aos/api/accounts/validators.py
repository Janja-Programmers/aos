"""Validators for profile updates."""

from __future__ import annotations

import re
import frappe

from aos.api.auth.responses import fail
from .constants import FULL_NAME_MIN_LEN, FULL_NAME_MAX_LEN, FILE_URL_ALLOWED_PREFIXES


def require_login():
    """Return current user or a fail(...) dict."""
    user = getattr(frappe.session, "user", None) or "Guest"
    if user == "Guest":
        return None, fail("Please login to continue.", code="UNAUTHORIZED")
    return user, None


def validate_full_name(value: str):
    value = (value or "").strip()
    if not value:
        return None, fail("Full name is required.", code="VALIDATION_ERROR")
    if len(value) < FULL_NAME_MIN_LEN:
        return None, fail("Full name is too short.", code="VALIDATION_ERROR")
    if len(value) > FULL_NAME_MAX_LEN:
        return None, fail("Full name is too long.", code="VALIDATION_ERROR")
    value = re.sub(r"\s+", " ", value)
    return value, None


def _get_file_by_url(file_url: str):
    if not file_url:
        return None
    return frappe.db.get_value(
        "File",
        {"file_url": file_url},
        ["name", "file_url", "attached_to_doctype", "attached_to_name", "is_private"],
        as_dict=True,
    )


def attach_file_to_user(file_url: str, *, current_user: str):
    """Attach File to the User doc if it isn't already attached.

    Helps when the client uploads without doctype/docname.
    """
    if not file_url:
        return

    f = _get_file_by_url(file_url)
    if not f:
        return

    if not f.attached_to_doctype and not f.attached_to_name:
        file_doc = frappe.get_doc("File", f.name)
        file_doc.attached_to_doctype = "User"
        file_doc.attached_to_name = current_user
        file_doc.save(ignore_permissions=True)


def validate_user_image(file_url: str, *, current_user: str):
    """Validate that the provided file_url exists and is usable by the user."""
    file_url = (file_url or "").strip()

    # allow clearing
    if file_url == "":
        return "", None

    # quick sanity check (optional; keeps garbage out)
    if not file_url.startswith(FILE_URL_ALLOWED_PREFIXES):
        return None, fail("Invalid image reference.", code="VALIDATION_ERROR")

    f = _get_file_by_url(file_url)
    if not f:
        return None, fail("Image not found. Please upload again.", code="NOT_FOUND")

    # Prevent using another user's attached file
    if f.attached_to_doctype and f.attached_to_name:
        if f.attached_to_doctype != "User" or f.attached_to_name != current_user:
            return None, fail("You don't have permission to use this image.", code="FORBIDDEN")

    return file_url, None
