"""Scheduled and manual hardening tasks for external-service jobs."""

from __future__ import annotations

from pprint import pformat

import frappe

from aos.utils.service_job_hardening import (
    audit_service_hardening,
    cleanup_service_job_payloads,
    get_service_job_status_summary,
)


def cleanup_external_service_jobs(dry_run: bool = False) -> dict:
    """Prune old payload bodies from terminal external-service job records."""
    return cleanup_service_job_payloads(dry_run=bool(dry_run))


def audit_external_service_hardening() -> dict:
    """Run source/layout audits for the external-service boundary."""
    result = audit_service_hardening()
    if not result.get("ok"):
        frappe.log_error(pformat(result), "AOS external service hardening audit failed")
    return result


def external_service_job_status_summary() -> dict:
    """Return grouped status counts for all external-service job DocTypes."""
    return get_service_job_status_summary()
