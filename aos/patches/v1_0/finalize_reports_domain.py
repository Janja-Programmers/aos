"""Reconcile existing Reports data into the final classified reason model.

Fresh sites receive the canonical reason catalog from ``after_install``. This
post-model-sync patch exists only so development/staging/test sites can migrate
cleanly onto the same final schema before the schema-only Report indexes run.
"""

from __future__ import annotations

import hashlib
import re

import frappe

from aos.services.reports.catalog import CANONICAL_REPORT_REASONS, install_canonical_report_reasons
from aos.services.reports.repository import active_report_key

_BATCH_SIZE = 250

_LEGACY_REASON_ALIASES = {
    "Spam": "spam",
    "Harassment": "harassment_abuse",
    "Harassment or abuse": "harassment_abuse",
    "Nudity or sexual content": "nudity_sexual_content",
    "Violence or dangerous content": "violence_dangerous_content",
    "Scam": "scam_fraud",
    "Scam or fraud": "scam_fraud",
    "Suspected scam or fraud": "scam_fraud",
    "Misleading or inaccurate description": "misleading_description",
    "Prohibited or restricted item": "prohibited_restricted_item",
    "Inappropriate content": "inappropriate_content",
    "Wrong category": "wrong_category",
    "Wrong or misleading pricing": "misleading_pricing",
    "Duplicate ad": "duplicate_ad",
    "Counterfeit or fake product": "counterfeit_product",
    "Other": "other",
}

_REPORT_TARGETS = (
    ("AOS User Report", "reported_user"),
    ("AOS Ad Report", "ad"),
    ("AOS Short Report", "short"),
)
_ALL_REASON_REPORTS = (
    "AOS User Report",
    "AOS Ad Report",
    "AOS Short Report",
    "AOS Review Report",
)


def execute() -> None:
    _initialize_legacy_reason_rows()
    install_canonical_report_reasons()
    migrated = _canonicalize_known_reason_links()
    disabled = _disable_noncanonical_reasons()
    rejected = _reconcile_reviewing_duplicates()
    _backfill_active_keys()
    _remove_unreferenced_known_alias_rows()

    frappe.logger("aos.reports", allow_site=True).info(
        "reports_domain_finalized reason_links_migrated=%s legacy_reasons_disabled=%s "
        "duplicate_reviewing_reports_rejected=%s",
        migrated,
        disabled,
        rejected,
    )


def _initialize_legacy_reason_rows() -> None:
    if not frappe.db.table_exists("AOS Report Reason"):
        return
    columns = _columns("AOS Report Reason")
    if not {"name", "reason_id", "label", "is_enabled"}.issubset(columns):
        return
    select = ["name", "reason_id", "label", "is_enabled"]
    if "title" in columns:
        select.append("title")
    if "is_active" in columns:
        select.append("is_active")
    rows = frappe.db.sql(
        f"SELECT {', '.join(f'`{field}`' for field in select)} FROM `tabAOS Report Reason`",
        as_dict=True,
    )
    canonical_ids = {str(item["reason_id"]) for item in CANONICAL_REPORT_REASONS}
    for row in rows:
        name = str(row.name or "")
        label = str(getattr(row, "label", "") or getattr(row, "title", "") or name).strip()[:140]
        reason_id = str(getattr(row, "reason_id", "") or "").strip()
        if not reason_id:
            # A partially migrated canonical row must keep its canonical document
            # identity. Legacy rows receive a collision-resistant disabled id.
            reason_id = name if name in canonical_ids else _legacy_reason_id(name)
        enabled = int(getattr(row, "is_enabled", 0) or 0)
        if not enabled and "is_active" in columns:
            enabled = int(getattr(row, "is_active", 0) or 0)
        # Known aliases are superseded by canonical rows created below. Unknown
        # legacy rows remain inspectable but are deliberately disabled so they
        # can never re-enter the public reason API accidentally.
        if name not in canonical_ids:
            enabled = 0
        frappe.db.sql(
            """
            UPDATE `tabAOS Report Reason`
            SET reason_id = %s, label = %s, is_enabled = %s
            WHERE name = %s
            """,
            (reason_id, label or name, enabled, name),
        )


def _canonicalize_known_reason_links() -> int:
    migrated = 0
    for doctype in _ALL_REASON_REPORTS:
        if not _supports(doctype, ["reason"]):
            continue
        table = f"tab{doctype}"
        for legacy, canonical in _LEGACY_REASON_ALIASES.items():
            if legacy == canonical:
                continue
            affected = frappe.db.sql(
                f"SELECT COUNT(*) FROM `{table}` WHERE reason = %s",
                (legacy,),
            )[0][0]
            if affected:
                frappe.db.sql(f"UPDATE `{table}` SET reason = %s WHERE reason = %s", (canonical, legacy))
                migrated += int(affected)
    return migrated


def _disable_noncanonical_reasons() -> int:
    if not _supports("AOS Report Reason", ["is_enabled"]):
        return 0
    canonical_ids = tuple(str(item["reason_id"]) for item in CANONICAL_REPORT_REASONS)
    rows = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabAOS Report Reason` WHERE name NOT IN %(names)s AND is_enabled != 0",
        {"names": canonical_ids},
    )
    count = int(rows[0][0] or 0)
    if count:
        frappe.db.sql(
            "UPDATE `tabAOS Report Reason` SET is_enabled = 0 WHERE name NOT IN %(names)s",
            {"names": canonical_ids},
        )
    return count


def _reconcile_reviewing_duplicates() -> int:
    rejected = 0
    for doctype, target_field in _REPORT_TARGETS:
        if not _supports(doctype, [target_field, "reported_by", "status", "active_key"]):
            continue
        table = f"tab{doctype}"
        while True:
            groups = frappe.db.sql(
                f"""
                SELECT `{target_field}` AS target_id, reported_by
                FROM `{table}`
                WHERE status = 'Reviewing'
                  AND COALESCE(`{target_field}`, '') != ''
                  AND COALESCE(reported_by, '') != ''
                GROUP BY `{target_field}`, reported_by
                HAVING COUNT(*) > 1
                ORDER BY `{target_field}`, reported_by
                LIMIT %s
                """,
                (_BATCH_SIZE,),
                as_dict=True,
            )
            if not groups:
                break
            for group in groups:
                rows = frappe.db.sql(
                    f"""
                    SELECT name
                    FROM `{table}`
                    WHERE `{target_field}` = %s AND reported_by = %s AND status = 'Reviewing'
                    ORDER BY creation ASC, name ASC
                    """,
                    (group.target_id, group.reported_by),
                    as_dict=True,
                )
                duplicate_names = tuple(str(row.name) for row in rows[1:])
                if not duplicate_names:
                    continue
                frappe.db.sql(
                    f"UPDATE `{table}` SET status = 'Rejected', active_key = NULL WHERE name IN %(names)s",
                    {"names": duplicate_names},
                )
                rejected += len(duplicate_names)
    return rejected


def _backfill_active_keys() -> None:
    for doctype, target_field in _REPORT_TARGETS:
        if not _supports(doctype, [target_field, "reported_by", "status", "active_key"]):
            continue
        table = f"tab{doctype}"
        frappe.db.sql(f"UPDATE `{table}` SET active_key = NULL WHERE status != 'Reviewing'")
        start_after = ""
        while True:
            rows = frappe.db.sql(
                f"""
                SELECT name, `{target_field}` AS target_id, reported_by
                FROM `{table}`
                WHERE name > %s
                  AND status = 'Reviewing'
                  AND COALESCE(`{target_field}`, '') != ''
                  AND COALESCE(reported_by, '') != ''
                ORDER BY name
                LIMIT %s
                """,
                (start_after, _BATCH_SIZE),
                as_dict=True,
            )
            if not rows:
                break
            for row in rows:
                frappe.db.set_value(
                    doctype,
                    row.name,
                    "active_key",
                    active_report_key(
                        doctype=doctype,
                        target_id=str(row.target_id),
                        reporter=str(row.reported_by),
                    ),
                    update_modified=False,
                )
            start_after = str(rows[-1].name)


def _remove_unreferenced_known_alias_rows() -> None:
    if not frappe.db.table_exists("AOS Report Reason"):
        return
    canonical = {str(item["reason_id"]) for item in CANONICAL_REPORT_REASONS}
    for legacy in _LEGACY_REASON_ALIASES:
        if legacy in canonical or not frappe.db.exists("AOS Report Reason", legacy):
            continue
        referenced = any(
            _supports(doctype, ["reason"]) and frappe.db.exists(doctype, {"reason": legacy})
            for doctype in _ALL_REASON_REPORTS
        )
        if referenced:
            continue
        if frappe.db.table_exists("AOS Report Reason Target"):
            frappe.db.delete("AOS Report Reason Target", {"parent": legacy})
        frappe.db.delete("AOS Report Reason", {"name": legacy})


def _legacy_reason_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    slug = slug[:48]
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    base = slug if slug and slug[0].isalpha() else "reason"
    return f"legacy_{base}_{digest}"[:64]


def _columns(doctype: str) -> set[str]:
    rows = frappe.db.sql(f"SHOW COLUMNS FROM `tab{doctype}`", as_dict=True)
    return {str(row.Field) for row in rows}


def _supports(doctype: str, fields: list[str]) -> bool:
    return bool(frappe.db.table_exists(doctype)) and all(
        frappe.db.has_column(doctype, field) for field in fields
    )
