"""Admin diagnostics for AOS operational readiness."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail, ok
from aos.utils.production_config import validate_production_config
from aos.utils.operational_health import validate_operational_health
from aos.utils.job_monitoring import validate_job_monitoring


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


@frappe.whitelist(methods=["GET", "POST"])
def get_operational_health_status():
    """Return a redacted operational-health report for admins only."""

    if not _is_admin_user():
        return fail(
            "Only a System Manager can view operational health diagnostics.",
            code="PERMISSION_DENIED",
        )

    report = validate_operational_health()
    return ok("Operational health validation complete.", report)

@frappe.whitelist(methods=["GET", "POST"])
def get_job_monitoring_status():
    """Return a redacted production job-monitoring report for admins only."""

    if not _is_admin_user():
        return fail(
            "Only a System Manager can view job monitoring diagnostics.",
            code="PERMISSION_DENIED",
        )

    report = validate_job_monitoring()
    return ok("Job monitoring validation complete.", report)

