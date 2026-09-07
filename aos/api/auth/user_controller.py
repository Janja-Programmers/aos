"""Narrow Frappe User-controller integration for AOS Authentication.

Frappe applies a coarse site-wide User-creation throttle in ``User.before_insert``.
That guard is useful for generic framework signup, but it would cap AOS public
registration across all users and all client IPs. AOS already applies shared
Redis limits by IP and identity before any User insert.

This mixin therefore suppresses only that framework guard for Website User
records that were explicitly marked by trusted AOS server code. The marker is
an in-process sentinel object, not a serializable/client-controlled flag. The
framework ``in_import`` flag is enabled only while the upstream
``User.before_insert`` method runs and is restored immediately afterwards, so
normal naming, validation, password handling, after-insert hooks, and generic
User creation keep their standard Frappe behavior.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document

_MANAGED_CREATION_FLAG = "aos_managed_website_user_creation"
_MANAGED_CREATION_SENTINEL = object()


def mark_aos_managed_website_user_creation(user: Any):
    """Mark an internal Website User insert as protected by AOS abuse controls.

    The value is intentionally an object-identity sentinel so request payloads,
    DocType fields, or serialized flags cannot opt themselves into the bypass.
    """
    if str(getattr(user, "user_type", "") or "") != "Website User":
        raise ValueError("AOS managed user creation is restricted to Website User records")
    user.flags[_MANAGED_CREATION_FLAG] = _MANAGED_CREATION_SENTINEL
    return user


class AOSAuthUserMixin(Document):
    """Extend Frappe User without replacing the framework controller."""

    def before_insert(self):
        managed = (
            str(getattr(self, "user_type", "") or "") == "Website User"
            and self.flags.get(_MANAGED_CREATION_FLAG) is _MANAGED_CREATION_SENTINEL
        )
        if not managed:
            return super().before_insert()

        # Frappe's User.before_insert currently delegates its global creation
        # guard to throttle_user_creation(), whose supported bypass is
        # frappe.flags.in_import. Scope that flag to this one upstream hook so
        # the rest of the document lifecycle retains normal framework behavior.
        previous = frappe.flags.get("in_import", False)
        frappe.flags.in_import = True
        try:
            return super().before_insert()
        finally:
            frappe.flags.in_import = previous
