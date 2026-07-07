"""Admin diagnostics for AOS operational readiness."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail, ok
from aos.utils.production_config import validate_production_config


def _is_admin_user(user: str | None = None) -> bool:
    user = user or getattr(frappe.session, "user", None)
    if not user or user == "Guest":
        return False
    if user == "Administrator":
        return True
    try:
        return "System Manager" in set(frappe.get_roles(user))
    except Exception:
        return False


@frappe.whitelist(methods=["GET", "POST"])
def get_production_config_status():
    """Return a redacted production-config readiness report for admins only."""

    if not _is_admin_user():
        return fail(
            "Only a System Manager can view production configuration diagnostics.",
            code="PERMISSION_DENIED",
        )

    report = validate_production_config()
    return ok("Production configuration validation complete.", report)
