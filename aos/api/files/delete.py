from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import DELETE_FILE_LIMIT_PER_MINUTE_PER_USER


def delete_file_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:files:delete:user:{current_user}",
        ttl_seconds=60,
        limit=DELETE_FILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    file_id = kwargs.get("file_id")

    if not file_id:
        return fail("File id is required.", code="VALIDATION_ERROR")

    file_doc = frappe.db.get_value(
        "File",
        file_id,
        ["name", "owner"],
        as_dict=True
    )

    if not file_doc:
        return fail("File not found.", code="NOT_FOUND")

    if file_doc.owner != current_user:
        return fail("You cannot delete this file.", code="PERMISSION_DENIED")

    try:
        frappe.delete_doc("File", file_id)

        return ok(
            "File deleted successfully.",
            data={"id": file_id}
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Delete File Failed")
        return fail("Failed to delete file.", code="INTERNAL_ERROR")
