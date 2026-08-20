"""Role Permission Manager-backed authorization helpers.

Use Frappe's permission engine for administrative DocType capabilities instead
of duplicating role names in application code. This keeps Custom DocPerm / Role
Permissions Manager as the runtime source of truth while domain ownership rules
remain explicit in their respective services.
"""

from __future__ import annotations

from typing import Any

import frappe


def has_doctype_permission(
    *,
    user: str | None,
    doctype: str,
    ptype: str = "read",
    doc: Any | None = None,
    docname: str | None = None,
) -> bool:
    """Return whether ``user`` has the requested Frappe DocType permission.

    ``doc`` / ``docname`` should be supplied when permission must be evaluated
    against a concrete record (for example media attached to a Category). The
    helper deliberately fails closed on invalid users, missing records, or
    permission-engine errors.
    """

    clean_user = str(user or "").strip()
    clean_doctype = str(doctype or "").strip()
    clean_ptype = str(ptype or "read").strip().lower() or "read"
    if not clean_user or clean_user == "Guest" or not clean_doctype:
        return False

    target = doc
    if target is None and docname:
        clean_name = str(docname or "").strip()
        if not clean_name:
            return False
        try:
            if not frappe.db.exists(clean_doctype, clean_name):
                return False
            target = frappe.get_doc(clean_doctype, clean_name)
        except Exception:
            return False

    try:
        return bool(
            frappe.has_permission(
                clean_doctype,
                ptype=clean_ptype,
                doc=target,
                user=clean_user,
            )
        )
    except Exception:
        return False
