"""Admin diagnostic endpoint implementations.

The package ``__init__`` module exposes only whitelisted wrappers.  Keep the
permission checks and readiness report assembly here so diagnostics follows the
same API structure as the rest of ``aos.api``.
"""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail, ok
from aos.utils.backup_readiness import validate_backup_readiness
from aos.utils.job_monitoring import validate_job_monitoring
from aos.utils.operational_health import validate_operational_health
from aos.utils.production_config import validate_production_config


def is_admin_user(user: str | None = None) -> bool:
    """Return whether the current/session user may access admin diagnostics."""

    user = user or getattr(frappe.session, "user", None)
    if not user or user == "Guest":
        return False
    if user == "Administrator":
        return True
    try:
        return "System Manager" in set(frappe.get_roles(user))
    except Exception:
        return False


def _require_admin_or_fail(message: str) -> dict | None:
    if is_admin_user():
        return None
    return fail(message, error="PERMISSION_DENIED")


def get_production_config_status_impl(**kwargs):
    """Return a redacted production-config readiness report for admins only."""

    permission_error = _require_admin_or_fail(
        "Only a System Manager can view production configuration diagnostics."
    )
    if permission_error:
        return permission_error

    report = validate_production_config()
    return ok("Production configuration validation complete.", report)


def get_operational_health_status_impl(**kwargs):
    """Return a redacted operational-health report for admins only."""

    permission_error = _require_admin_or_fail(
        "Only a System Manager can view operational health diagnostics."
    )
    if permission_error:
        return permission_error

    report = validate_operational_health()
    return ok("Operational health validation complete.", report)


def get_job_monitoring_status_impl(**kwargs):
    """Return a redacted production job-monitoring report for admins only."""

    permission_error = _require_admin_or_fail(
        "Only a System Manager can view job monitoring diagnostics."
    )
    if permission_error:
        return permission_error

    report = validate_job_monitoring()
    return ok("Job monitoring validation complete.", report)


def get_backup_readiness_status_impl(**kwargs):
    """Return a redacted backup/restore-readiness report for admins only."""

    permission_error = _require_admin_or_fail(
        "Only a System Manager can view backup readiness diagnostics."
    )
    if permission_error:
        return permission_error

    report = validate_backup_readiness()
    return ok("Backup readiness validation complete.", report)
