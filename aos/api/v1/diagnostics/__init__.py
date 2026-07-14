"""Public AOS API v1 wrappers for diagnostics.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.diagnostics.*.
Implementation stays in aos.api.diagnostics implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.diagnostics.status import (
    get_production_config_status_impl as _get_production_config_status_impl,
    get_operational_health_status_impl as _get_operational_health_status_impl,
    get_job_monitoring_status_impl as _get_job_monitoring_status_impl,
    get_backup_readiness_status_impl as _get_backup_readiness_status_impl,
)

@frappe.whitelist(methods=["GET", "POST"])
def get_production_config_status(**kwargs):
    """Execute the v1 diagnostics.get_production_config_status endpoint."""
    return _get_production_config_status_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_operational_health_status(**kwargs):
    """Execute the v1 diagnostics.get_operational_health_status endpoint."""
    return _get_operational_health_status_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_job_monitoring_status(**kwargs):
    """Execute the v1 diagnostics.get_job_monitoring_status endpoint."""
    return _get_job_monitoring_status_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_backup_readiness_status(**kwargs):
    """Execute the v1 diagnostics.get_backup_readiness_status endpoint."""
    return _get_backup_readiness_status_impl(**kwargs)
