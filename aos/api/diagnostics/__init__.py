"""Admin diagnostics API wrappers."""

from __future__ import annotations

import frappe

from .status import (
    get_backup_readiness_status_impl,
    get_job_monitoring_status_impl,
    get_operational_health_status_impl,
    get_production_config_status_impl,
)


@frappe.whitelist(methods=["GET", "POST"])
def get_production_config_status(**kwargs):
    return get_production_config_status_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_operational_health_status(**kwargs):
    return get_operational_health_status_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_job_monitoring_status(**kwargs):
    return get_job_monitoring_status_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_backup_readiness_status(**kwargs):
    return get_backup_readiness_status_impl(**kwargs)
