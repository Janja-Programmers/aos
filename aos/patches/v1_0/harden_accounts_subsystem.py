"""Migrate AOS Profile to the canonical ACC-* primary key and add operational indexes.

The application is installed on fresh sites, so current DocType source is the
canonical schema. This patch remains upgrade-safe for existing v10 staging data:
if the old ``public_id`` column is still present it renames each profile to that
opaque id once, then all runtime code uses ``AOS Profile.name`` as the account id.
"""

from __future__ import annotations

import frappe

from aos.services.accounts.identity import generate_public_account_id, normalize_public_account_id

INDEXES = {
    "idx_aos_profile_lifecycle": ("AOS Profile", ["account_status", "restore_deadline"]),
    "idx_aos_profile_purge_scan": ("AOS Profile", ["account_status", "purge_status", "restore_deadline"]),
}


def execute() -> None:
    if not frappe.db.table_exists("AOS Profile"):
        return
    _migrate_profile_primary_keys()
    _backfill_display_names()
    _clear_frappe_profile_duplicates()
    _retire_legacy_profile_columns()
    for name, (doctype, fields) in INDEXES.items():
        if all(frappe.db.has_column(doctype, field) for field in fields) and not _index_exists(doctype, name):
            frappe.db.add_index(doctype, fields, index_name=name)
    frappe.logger("aos.accounts", allow_site=True).info("accounts_schema_hardening_complete")


def _columns() -> set[str]:
    return {str(row[0]) for row in frappe.db.sql("SHOW COLUMNS FROM `tabAOS Profile`")}


def _migrate_profile_primary_keys() -> None:
    """Upgrade v10 rows where profile.name was User.name and public_id held ACC-*.

    Fresh v11 sites have no ``public_id`` column, so this is a no-op there.
    ``rename_doc`` keeps framework-managed references consistent; AOS business
    relationships use ``AOS Profile.user`` rather than the profile primary key.
    """
    if "public_id" not in _columns():
        return
    rows = frappe.db.sql(
        "SELECT name, user, public_id FROM `tabAOS Profile` ORDER BY creation ASC, name ASC",
        as_dict=True,
    )
    for row in rows:
        old_name = str(row.name or "").strip()
        target = normalize_public_account_id(row.public_id)
        if not target:
            for _ in range(8):
                candidate = generate_public_account_id()
                if not frappe.db.exists("AOS Profile", candidate):
                    target = candidate
                    break
        if not target:
            frappe.throw("Unable to allocate account id during profile migration.", frappe.ValidationError)
        if old_name == target:
            continue
        if frappe.db.exists("AOS Profile", target):
            frappe.throw("Duplicate AOS account id during profile migration.", frappe.ValidationError)
        frappe.rename_doc("AOS Profile", old_name, target, force=True)


def _backfill_display_names() -> None:
    if "display_name" not in _columns():
        return
    rows = frappe.db.sql(
        "SELECT name, user, display_name FROM `tabAOS Profile` ORDER BY name",
        as_dict=True,
    )
    for row in rows:
        if str(row.display_name or "").strip():
            continue
        display_name = frappe.db.get_value("User", row.user, "full_name") or "AOS User"
        frappe.db.set_value("AOS Profile", row.name, "display_name", str(display_name).strip()[:80], update_modified=False)



def _clear_frappe_profile_duplicates() -> None:
    """Clear AOS-owned profile values from Frappe User after v10 upgrade.

    Display name and user_image intentionally remain framework projections.
    """
    if not frappe.db.table_exists("User"):
        return
    columns = {str(row[0]) for row in frappe.db.sql("SHOW COLUMNS FROM `tabUser`")}
    retired = [field for field in ("bio", "mobile_no", "birth_date", "gender", "location") if field in columns]
    if not retired:
        return
    assignments = ", ".join(f"u.`{field}` = NULL" for field in retired)
    frappe.db.sql(
        f"""
        UPDATE `tabUser` u
        INNER JOIN `tabAOS Profile` p ON p.user = u.name
        SET {assignments}
        WHERE u.user_type = 'Website User'
        """
    )


def _retire_legacy_profile_columns() -> None:
    """Erase values from retired v10 columns that MariaDB may retain physically."""
    columns = _columns()
    assignments: list[str] = []
    for field in ("public_id", "location", "verified_by", "verified_on", "deactivated_at"):
        if field in columns:
            assignments.append(f"`{field}` = NULL")
    if "is_deleted" in columns:
        assignments.append("`is_deleted` = 0")
    if assignments:
        frappe.db.sql(f"UPDATE `tabAOS Profile` SET {', '.join(assignments)}")

def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT INDEX_NAME FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )
