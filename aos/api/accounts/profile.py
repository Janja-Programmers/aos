"""Profile (User) implementation.

These functions contain the business logic. Public whitelisted wrappers live in
accounts/__init__.py to match the existing auth structure.
"""

from __future__ import annotations

import frappe

from aos.api.auth.responses import ok, fail

from .constants import EDITABLE_USER_FIELDS
from .serializers import serialize_user
from .validators import (
    require_login,
    validate_full_name,
    validate_user_image,
    attach_file_to_user,
)


def get_profile_impl():
    current_user, err = require_login()
    if err:
        return err

    try:
        user_doc = frappe.get_doc("User", current_user)
        return ok("Profile fetched.", data=serialize_user(user_doc))
    except frappe.DoesNotExistError:
        return fail("User not found.", code="NOT_FOUND")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Profile Failed")
        return fail("Failed to fetch profile.", code="INTERNAL_ERROR")


def update_profile_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    incoming = {k: v for k, v in (kwargs or {}).items() if k in EDITABLE_USER_FIELDS}
    if not incoming:
        return fail("No editable fields provided.", code="VALIDATION_ERROR")

    try:
        user_doc = frappe.get_doc("User", current_user)

        if "full_name" in incoming:
            full_name, e = validate_full_name(incoming.get("full_name"))
            if e:
                return e
            user_doc.first_name = full_name

        if "user_image" in incoming:
            file_url, e = validate_user_image(incoming.get("user_image"), current_user=current_user)
            if e:
                return e

            attach_file_to_user(file_url, current_user=current_user)
            user_doc.user_image = file_url or ""

        user_doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok("Profile updated.", data=serialize_user(user_doc))

    except frappe.DoesNotExistError:
        return fail("User not found.", code="NOT_FOUND")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Update Profile Failed")
        return fail("Failed to update profile.", code="INTERNAL_ERROR")
