"""Redacted, read-only migration preflight for controlled deployment.

This detects common operational blockers; it does not prove that every schema
change is non-destructive. Operators must still review pending patches and the
release migration diff before production approval.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import frappe

_REQUIRED_OUTBOX_COLUMNS = {
    "name",
    "status",
    "service_type",
    "idempotency_key",
    "attempt_count",
    "max_attempts",
    "claim_token",
    "lease_expires_at",
    "callback_deadline_at",
}


def _check(
    checks: list[dict[str, Any]],
    name: str,
    severity: str,
    ready: bool,
    message: str,
    **details: Any,
) -> None:
    checks.append(
        {
            "name": name,
            "severity": severity,
            "ready": bool(ready),
            "message": message,
            "details": details,
        }
    )


def _guard(
    checks: list[dict[str, Any]],
    name: str,
    severity: str,
    function: Callable[[], tuple[bool, str, dict[str, Any]]],
) -> None:
    try:
        ready, message, details = function()
        _check(checks, name, severity, ready, message, **details)
    except Exception:
        _check(checks, name, severity, False, "Check could not be completed safely.")


def _disk_check() -> tuple[bool, str, dict[str, Any]]:
    site_path = Path(getattr(frappe.local, "site_path", "") or os.getcwd())
    usage = shutil.disk_usage(site_path)
    minimum_bytes = max(1, int(os.getenv("AOS_MIGRATION_MIN_FREE_GB", "5"))) * 1024**3
    ready = usage.free >= minimum_bytes
    return ready, "Deployment filesystem has sufficient headroom." if ready else "Deployment filesystem free space is below the configured minimum.", {
        "free_bytes": int(usage.free),
        "minimum_free_bytes": minimum_bytes,
    }


def _database_space_check() -> tuple[bool, str, dict[str, Any]]:
    rows = frappe.db.sql(
        """
        SELECT COALESCE(SUM(data_free), 0)
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
        """
    )
    reclaimable = int((rows or [[0]])[0][0] or 0)
    return True, "Database free-space metadata is readable.", {"reported_reclaimable_bytes": reclaimable}


def _transaction_check() -> tuple[bool, str, dict[str, Any]]:
    threshold = max(30, int(os.getenv("AOS_MIGRATION_LONG_TRANSACTION_SECONDS", "300")))
    rows = frappe.db.sql(
        """
        SELECT COUNT(*)
        FROM information_schema.innodb_trx
        WHERE TIMESTAMPDIFF(SECOND, trx_started, UTC_TIMESTAMP()) >= %s
        """,
        (threshold,),
    )
    count = int((rows or [[0]])[0][0] or 0)
    return count == 0, "No long-running transactions block migration." if count == 0 else "Long-running database transactions require operator review.", {
        "long_running_transaction_count": count,
        "threshold_seconds": threshold,
    }


def _metadata_lock_check() -> tuple[bool, str, dict[str, Any]]:
    rows = frappe.db.sql(
        """
        SELECT COUNT(*)
        FROM performance_schema.metadata_locks
        WHERE LOCK_STATUS = 'PENDING'
        """
    )
    count = int((rows or [[0]])[0][0] or 0)
    return count == 0, "No pending metadata locks were found." if count == 0 else "Pending metadata locks may block migration.", {
        "pending_metadata_lock_count": count,
    }


def _outbox_check() -> tuple[bool, str, dict[str, Any]]:
    rows = frappe.db.sql(
        """
        SELECT status, COUNT(*) AS total
        FROM `tabAOS Transactional Outbox`
        GROUP BY status
        """,
        as_dict=True,
    )
    counts = {str(row.status): int(row.total or 0) for row in rows}
    dead = counts.get("Dead Letter", 0)
    queued = counts.get("Queued", 0) + counts.get("Failed", 0)
    maximum = max(0, int(os.getenv("AOS_MIGRATION_MAX_OUTBOX_BACKLOG", "5000")))
    ready = dead == 0 and queued <= maximum
    return ready, "Outbox backlog is within deployment policy." if ready else "Outbox dead letters or backlog exceed deployment policy.", {
        "queued_retryable_count": queued,
        "dead_letter_count": dead,
        "maximum_backlog": maximum,
    }


def _worker_check() -> tuple[bool, str, dict[str, Any]]:
    from frappe.utils.background_jobs import get_workers

    workers = list(get_workers() or [])
    return bool(workers), "At least one Frappe worker is available." if workers else "No Frappe workers are currently visible.", {
        "worker_count": len(workers),
    }



def _environment() -> str:
    return str(os.getenv("AOS_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "development").strip().lower()


def _previous_migration_failure_check() -> tuple[bool, str, dict[str, Any]]:
    marker = Path(os.getenv("AOS_MIGRATION_FAILURE_MARKER", "/var/lib/aos/deployment/last-migration-failed.env"))
    exists = marker.is_file()
    return (
        not exists,
        "No unresolved migration-failure marker is present." if not exists else "A previous migration failure marker requires operator resolution.",
        {"failure_marker_present": exists},
    )

def validate_migration_preflight() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    _guard(
        checks,
        "database_connection",
        "error",
        lambda: (bool(frappe.db.sql("SELECT 1")), "Database connectivity succeeded.", {}),
    )

    def pending_patches() -> tuple[bool, str, dict[str, Any]]:
        pending = list(frappe.get_attr("frappe.modules.patch_handler.get_pending_patches")() or [])
        return True, "Pending patch state is readable; release review remains required.", {
            "pending_patch_count": len(pending),
            "manual_patch_review_required": bool(pending),
        }

    _guard(checks, "pending_patches", "error", pending_patches)
    _guard(checks, "previous_migration_failure", "error", _previous_migration_failure_check)

    def installed_app() -> tuple[bool, str, dict[str, Any]]:
        installed = set(frappe.get_installed_apps())
        ready = "aos" in installed
        return ready, "AOS is installed on the target site." if ready else "AOS is not installed on the target site.", {}

    _guard(checks, "aos_installed", "error", installed_app)

    def schema_check() -> tuple[bool, str, dict[str, Any]]:
        table_exists = bool(frappe.db.table_exists("AOS Transactional Outbox"))
        columns = set(frappe.db.get_table_columns("AOS Transactional Outbox")) if table_exists else set()
        missing = sorted(_REQUIRED_OUTBOX_COLUMNS - columns)
        return table_exists and not missing, "Required outbox migration schema is present." if table_exists and not missing else "Required outbox table or columns are missing.", {
            "missing_column_count": len(missing),
            "missing_columns": missing,
        }

    _guard(checks, "required_schema", "error", schema_check)
    _guard(checks, "filesystem_headroom", "error", _disk_check)
    _guard(checks, "database_space_metadata", "warning", _database_space_check)
    _guard(checks, "long_running_transactions", "error", _transaction_check)
    _guard(checks, "pending_metadata_locks", "error", _metadata_lock_check)

    def backup_check() -> tuple[bool, str, dict[str, Any]]:
        from aos.utils.backup_readiness import validate_backup_readiness

        report = validate_backup_readiness()
        return bool(report.get("ready")), "Backup, encryption, offsite, and rehearsal gates are ready." if report.get("ready") else "Backup readiness blocks production migration.", {
            "unhealthy_check_count": int((report.get("counts") or {}).get("unhealthy") or 0),
        }

    backup_severity = "error" if _environment() == "production" else "warning"
    _guard(checks, "backup_readiness", backup_severity, backup_check)
    _guard(checks, "outbox_backlog", "error", _outbox_check)
    _guard(checks, "worker_queue_health", "error", _worker_check)

    _check(
        checks,
        "manual_migration_review",
        "warning",
        True,
        "Preflight cannot prove that every custom patch or schema operation is non-destructive; review the release migration diff manually.",
        manual_review_required=True,
    )

    blockers = [item["name"] for item in checks if item["severity"] == "error" and not item["ready"]]
    warnings = [item["name"] for item in checks if item["severity"] == "warning" and not item["ready"]]
    return {
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "manual_review_required": True,
    }


def assert_migration_preflight_ready() -> dict[str, Any]:
    report = validate_migration_preflight()
    if not report["ready"]:
        raise RuntimeError("Migration preflight found production deployment blockers.")
    return report
