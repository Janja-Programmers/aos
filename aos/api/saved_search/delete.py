from __future__ import annotations
import frappe
from aos.api.shared.auth import require_login
from aos.api.shared.responses import ok, fail

_DT = "AOS Saved Search"


def delete_saved_search_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    search_id = kwargs.get("id")
    if not search_id:
        return fail("Search id required.", code="VALIDATION_ERROR")

    doc = frappe.get_doc(_DT, search_id)

    if doc.user != user:
        return fail("Forbidden.", code="FORBIDDEN")

    doc.is_active = 0
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return ok("Search deleted.")
