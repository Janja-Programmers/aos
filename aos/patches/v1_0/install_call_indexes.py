"""Idempotent schema indexes for canonical multi-participant Calls."""
from __future__ import annotations

from collections.abc import Sequence
import frappe

INDEXES = (
    ("AOS Call", "uq_call_room_name", ("room_name",), True),
    ("AOS Call", "idx_call_initiator_active", ("initiator", "is_active", "status", "creation", "name"), False),
    ("AOS Call", "idx_call_conversation_state", ("conversation", "is_active", "status", "creation", "name"), False),
    ("AOS Call", "idx_call_room_cleanup", ("room_cleanup_pending", "status", "modified", "name"), False),
    ("AOS Call", "idx_call_reconcile", ("status", "is_active", "rtc_last_checked_at", "started_at", "name"), False),
    ("AOS Call Participant", "uq_call_participant", ("call", "user"), True),
    ("AOS Call Participant", "idx_call_participant_user_active", ("user", "status", "call", "name"), False),
    ("AOS Call Participant", "idx_call_participant_call_state", ("call", "status", "user", "name"), False),
    ("AOS Call Participant", "idx_call_participant_history", ("user", "visible", "creation", "name"), False),
    ("AOS Call Participant", "idx_call_participant_expiry", ("status", "ring_expires_at", "call", "name"), False),
)

LEGACY_INDEXES = (
    "idx_call_caller_active", "idx_call_receiver_active", "idx_call_caller_history",
    "idx_call_receiver_history", "idx_call_ring_expiry", "idx_call_timeout",
)


def execute() -> None:
    _drop_legacy_indexes()
    for doctype, name, columns, unique in INDEXES:
        _ensure_index(doctype, name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _exists(doctype: str, name: str) -> bool:
    return bool(frappe.db.sql("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1", (_table(doctype), name)))


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Calls schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Calls schema columns are missing for {doctype}: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, columns: Sequence[str], name: str) -> None:
    non_null = " AND ".join(f"`{column}` IS NOT NULL AND `{column}`!=''" for column in columns)
    groups = ", ".join(f"`{column}`" for column in columns)
    if frappe.db.sql(f"SELECT 1 FROM `{_table(doctype)}` WHERE {non_null} GROUP BY {groups} HAVING COUNT(*)>1 LIMIT 1"):
        frappe.throw(f"Cannot install Calls unique index {name}; duplicate data remains in {doctype}")


def _quote_identifier(value: str) -> str:
    # Frappe's MariaDB add_index/add_unique helpers do not quote field names.
    # Calls intentionally uses Link fields named `call` and `user`, both of
    # which can collide with SQL keywords/functions. All identifiers in this
    # installer are static application schema identifiers; quote them here so
    # the migration is valid and repeatable on MariaDB.
    return "`" + value.replace("`", "``") + "`"


def _add_index_ddl(doctype: str, name: str, columns: Sequence[str], *, unique: bool) -> None:
    table = _quote_identifier(_table(doctype))
    index = _quote_identifier(name)
    fields = ", ".join(_quote_identifier(column) for column in columns)
    kind = "UNIQUE INDEX" if unique else "INDEX"
    frappe.db.sql_ddl(f"ALTER TABLE {table} ADD {kind} {index} ({fields})")


def _ensure_index(doctype: str, name: str, columns: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(doctype, columns)
    if _exists(doctype, name):
        return
    if unique:
        _assert_unique_ready(doctype, columns, name)
    _add_index_ddl(doctype, name, columns, unique=unique)


def _drop_legacy_indexes() -> None:
    if not frappe.db.table_exists("AOS Call"):
        return
    for name in LEGACY_INDEXES:
        if _exists("AOS Call", name):
            frappe.db.sql_ddl(f"ALTER TABLE `tabAOS Call` DROP INDEX `{name}`")
