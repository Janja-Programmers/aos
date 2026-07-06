"""Operational hardening helpers for AOS external-service jobs.

These utilities are intentionally Frappe-side and lightweight. They do not run
video processing, moderation, ranking, notification delivery, or analytics work;
they only summarize/audit durable Frappe job records and prune bulky payloads
according to retention settings.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frappe
from frappe.utils import add_days, now_datetime

from aos.utils.aos_config import get_env_bool, get_env_int


@dataclass(frozen=True)
class ServiceJobSpec:
    label: str
    doctype: str
    success_statuses: tuple[str, ...]
    failed_statuses: tuple[str, ...] = ("Failed",)
    payload_fields: tuple[str, ...] = ("request_payload", "response_payload")

    @property
    def terminal_statuses(self) -> tuple[str, ...]:
        return self.success_statuses + self.failed_statuses + ("Cancelled",)


SERVICE_JOB_SPECS: tuple[ServiceJobSpec, ...] = (
    ServiceJobSpec(
        label="video_processing",
        doctype="AOS Video Processing Job",
        success_statuses=("Ready",),
    ),
    ServiceJobSpec(
        label="moderation",
        doctype="AOS Moderation Job",
        success_statuses=("Allowed", "Review Required", "Rejected"),
    ),
    ServiceJobSpec(
        label="search_ranking",
        doctype="AOS Search Index Job",
        success_statuses=("Indexed", "Deleted"),
    ),
    ServiceJobSpec(
        label="notification_delivery",
        doctype="AOS Notification Delivery Job",
        success_statuses=("Delivered", "Skipped"),
        payload_fields=("payload_json", "request_payload", "response_payload"),
    ),
    ServiceJobSpec(
        label="analytics_pipeline",
        doctype="AOS Analytics Ingest Job",
        success_statuses=("Ingested", "Skipped"),
        payload_fields=("events_json", "request_payload", "response_payload", "counters_json"),
    ),
)

SERVICE_API_MODULES: tuple[str, ...] = (
    "video_processing",
    "moderation",
    "search_ranking",
    "notification_delivery",
    "analytics_pipeline",
)

_ALLOWED_WRAPPER_IMPORTS = {
    "frappe",
}

_ALLOWED_WRAPPER_FROM_MODULE_PREFIXES = (
    ".",
    "__future__",
)


def get_service_job_retention_config() -> dict[str, int | bool]:
    """Return retention settings for the durable service-job records.

    Defaults are conservative: successful payload bodies are pruned after 30
    days, failed payload bodies after 90 days, and full job deletion is disabled.
    """
    return {
        "enabled": get_env_bool("SERVICE_JOB_CLEANUP_ENABLED", True),
        "success_payload_retention_days": get_env_int(
            "SERVICE_JOB_SUCCESS_PAYLOAD_RETENTION_DAYS",
            30,
            min_value=1,
            max_value=3650,
        ),
        "failed_payload_retention_days": get_env_int(
            "SERVICE_JOB_FAILED_PAYLOAD_RETENTION_DAYS",
            90,
            min_value=1,
            max_value=3650,
        ),
        "delete_success_after_days": get_env_int(
            "SERVICE_JOB_DELETE_SUCCESS_AFTER_DAYS",
            0,
            min_value=0,
            max_value=3650,
        ),
        "delete_failed_after_days": get_env_int(
            "SERVICE_JOB_DELETE_FAILED_AFTER_DAYS",
            0,
            min_value=0,
            max_value=3650,
        ),
        "limit": get_env_int("SERVICE_JOB_CLEANUP_LIMIT", 500, min_value=1, max_value=5000),
    }


def get_service_job_specs() -> list[dict[str, Any]]:
    """Return serializable service-job specs for docs/admin consoles."""
    return [
        {
            "label": spec.label,
            "doctype": spec.doctype,
            "success_statuses": list(spec.success_statuses),
            "failed_statuses": list(spec.failed_statuses),
            "terminal_statuses": list(spec.terminal_statuses),
            "payload_fields": list(spec.payload_fields),
        }
        for spec in SERVICE_JOB_SPECS
        if frappe.db.exists("DocType", spec.doctype)
    ]


def get_service_job_status_summary() -> dict[str, dict[str, int]]:
    """Return status counts for every external-service job DocType."""
    summary: dict[str, dict[str, int]] = {}
    for spec in SERVICE_JOB_SPECS:
        if not frappe.db.exists("DocType", spec.doctype):
            summary[spec.doctype] = {"__missing_doctype__": 1}
            continue
        rows = frappe.db.sql(
            f"""
            SELECT status, COUNT(*) AS total
            FROM `tab{spec.doctype}`
            GROUP BY status
            ORDER BY status
            """,
            as_dict=True,
        )
        summary[spec.doctype] = {str(row.status or ""): int(row.total or 0) for row in rows}
    return summary


def cleanup_service_job_payloads(*, dry_run: bool = False) -> dict[str, Any]:
    """Prune bulky request/response payloads from old terminal jobs.

    The durable job rows remain in Frappe for audit/status history. Only payload
    fields are nulled. Full deletion is available through env variables but is
    disabled by default.
    """
    config = get_service_job_retention_config()
    if not bool(config["enabled"]):
        return {"ok": True, "enabled": False, "pruned": {}, "deleted": {}}

    limit = int(config["limit"])
    pruned: dict[str, int] = {}
    deleted: dict[str, int] = {}

    for spec in SERVICE_JOB_SPECS:
        if not frappe.db.exists("DocType", spec.doctype):
            continue

        pruned[spec.doctype] = 0
        deleted[spec.doctype] = 0

        success_cutoff = add_days(now_datetime(), -int(config["success_payload_retention_days"]))
        failed_cutoff = add_days(now_datetime(), -int(config["failed_payload_retention_days"]))

        pruned[spec.doctype] += _prune_payloads_for_statuses(
            spec=spec,
            statuses=spec.success_statuses,
            cutoff=success_cutoff,
            limit=limit,
            dry_run=dry_run,
        )
        pruned[spec.doctype] += _prune_payloads_for_statuses(
            spec=spec,
            statuses=spec.failed_statuses + ("Cancelled",),
            cutoff=failed_cutoff,
            limit=limit,
            dry_run=dry_run,
        )

        delete_success_after_days = int(config["delete_success_after_days"])
        if delete_success_after_days > 0:
            deleted[spec.doctype] += _delete_old_jobs_for_statuses(
                spec=spec,
                statuses=spec.success_statuses,
                cutoff=add_days(now_datetime(), -delete_success_after_days),
                limit=limit,
                dry_run=dry_run,
            )

        delete_failed_after_days = int(config["delete_failed_after_days"])
        if delete_failed_after_days > 0:
            deleted[spec.doctype] += _delete_old_jobs_for_statuses(
                spec=spec,
                statuses=spec.failed_statuses + ("Cancelled",),
                cutoff=add_days(now_datetime(), -delete_failed_after_days),
                limit=limit,
                dry_run=dry_run,
            )

    if not dry_run:
        frappe.db.commit()

    return {
        "ok": True,
        "enabled": True,
        "dry_run": dry_run,
        "config": config,
        "pruned": pruned,
        "deleted": deleted,
    }


def _prune_payloads_for_statuses(
    *,
    spec: ServiceJobSpec,
    statuses: tuple[str, ...],
    cutoff,
    limit: int,
    dry_run: bool,
) -> int:
    if not statuses:
        return 0

    filters = {
        "status": ["in", list(statuses)],
        "modified": ["<", cutoff],
    }
    rows = frappe.get_all(
        spec.doctype,
        filters=filters,
        fields=["name", *spec.payload_fields],
        order_by="modified asc",
        limit=limit,
    )

    count = 0
    for row in rows:
        has_payload = any(row.get(field) for field in spec.payload_fields)
        if not has_payload:
            continue
        count += 1
        if dry_run:
            continue
        updates = {field: None for field in spec.payload_fields}
        frappe.db.set_value(spec.doctype, row.name, updates, update_modified=False)
    return count


def _delete_old_jobs_for_statuses(
    *,
    spec: ServiceJobSpec,
    statuses: tuple[str, ...],
    cutoff,
    limit: int,
    dry_run: bool,
) -> int:
    if not statuses:
        return 0
    rows = frappe.get_all(
        spec.doctype,
        filters={"status": ["in", list(statuses)], "modified": ["<", cutoff]},
        pluck="name",
        order_by="modified asc",
        limit=limit,
    )
    if dry_run:
        return len(rows)
    for name in rows:
        frappe.delete_doc(spec.doctype, name, ignore_permissions=True, force=True)
    return len(rows)


def audit_reserved_enqueue_job_id_kwargs() -> dict[str, Any]:
    """Find reserved job_id keyword usage in Frappe enqueue calls.

    Frappe/RQ reserves the `job_id` keyword for the Redis job ID. AOS service
    dispatchers should use feature-specific names such as video_job_id,
    search_job_id, delivery_job_id, or analytics_job_id instead.
    """
    app_path = Path(frappe.get_app_path("aos")).resolve()
    findings: list[dict[str, Any]] = []
    for path in app_path.rglob("*.py"):
        if any(part == "__pycache__" for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:
            findings.append({"file": str(path.relative_to(app_path)), "line": None, "error": str(exc)})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_frappe_enqueue_call(node.func):
                continue
            for keyword in node.keywords:
                if keyword.arg == "job_id":
                    findings.append({
                        "file": str(path.relative_to(app_path)),
                        "line": getattr(node, "lineno", None),
                        "keyword": "job_id",
                    })
    return {"ok": not findings, "findings": findings}


def _is_frappe_enqueue_call(func: ast.AST) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "enqueue"
        and isinstance(func.value, ast.Name)
        and func.value.id == "frappe"
    )


def audit_service_api_wrappers() -> dict[str, Any]:
    """Audit that service API __init__.py files are whitelisted wrappers only."""
    app_path = Path(frappe.get_app_path("aos")).resolve()
    results: dict[str, Any] = {}
    for module in SERVICE_API_MODULES:
        path = app_path / "api" / module / "__init__.py"
        if not path.exists():
            results[module] = {"ok": False, "errors": ["missing __init__.py"]}
            continue
        results[module] = _audit_wrapper_file(path, app_path)
    return {"ok": all(item.get("ok") for item in results.values()), "modules": results}


def _audit_wrapper_file(path: Path, app_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "file": str(path.relative_to(app_path)), "errors": [str(exc)]}

    for node in tree.body:
        if isinstance(node, (ast.Expr, ast.Import, ast.ImportFrom)):
            _audit_import_node(node, errors)
            continue
        if isinstance(node, ast.FunctionDef):
            if not _has_frappe_whitelist_decorator(node):
                errors.append(f"function {node.name} is not a whitelisted wrapper")
            if not _function_is_simple_wrapper(node):
                errors.append(f"function {node.name} contains non-wrapper logic")
            continue
        errors.append(f"unexpected top-level {type(node).__name__} at line {getattr(node, 'lineno', '?')}")

    return {"ok": not errors, "file": str(path.relative_to(app_path)), "errors": errors}


def _audit_import_node(node: ast.AST, errors: list[str]) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name not in _ALLOWED_WRAPPER_IMPORTS:
                errors.append(f"unexpected import {alias.name}")
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if not (node.level > 0 or module in _ALLOWED_WRAPPER_FROM_MODULE_PREFIXES):
            errors.append(f"unexpected from-import {module}")


def _has_frappe_whitelist_decorator(node: ast.FunctionDef) -> bool:
    for decorator in node.decorator_list:
        call = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(call, ast.Attribute)
            and call.attr == "whitelist"
            and isinstance(call.value, ast.Name)
            and call.value.id == "frappe"
        ):
            return True
    return False


def _function_is_simple_wrapper(node: ast.FunctionDef) -> bool:
    """Return true for wrappers that only return/call an imported impl."""
    statements = [stmt for stmt in node.body if not isinstance(stmt, ast.Expr) or not isinstance(getattr(stmt, "value", None), ast.Constant)]
    if len(statements) != 1:
        return False
    stmt = statements[0]
    if isinstance(stmt, ast.Return):
        return isinstance(stmt.value, ast.Call)
    if isinstance(stmt, ast.Expr):
        return isinstance(stmt.value, ast.Call)
    return False


def audit_service_hardening() -> dict[str, Any]:
    """Run all service hardening audits used before production releases."""
    reserved = audit_reserved_enqueue_job_id_kwargs()
    wrappers = audit_service_api_wrappers()
    return {
        "ok": bool(reserved.get("ok")) and bool(wrappers.get("ok")),
        "reserved_enqueue_job_id_kwargs": reserved,
        "service_api_wrappers": wrappers,
        "job_status_summary": get_service_job_status_summary(),
    }
