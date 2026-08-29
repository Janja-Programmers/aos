"""Database helpers shared by API modules."""

from __future__ import annotations

from typing import Any

import frappe


def is_duplicate_entry_error(exc: BaseException) -> bool:
    """Return True when an exception represents a DB unique violation.

    Frappe may surface unique-index races as either ``DuplicateEntryError`` or
    ``UniqueValidationError`` depending on whether validation or the database
    constraint catches the duplicate first. MariaDB/PyMySQL errors can also be
    wrapped, so keep a final text/code fallback for raw integrity errors.
    """

    duplicate_classes = tuple(
        cls
        for cls in (
            getattr(frappe, "DuplicateEntryError", None),
            getattr(frappe, "UniqueValidationError", None),
        )
        if cls is not None
    )

    if duplicate_classes and isinstance(exc, duplicate_classes):
        return True

    text = " ".join(str(part) for part in getattr(exc, "args", ()) if part)
    text = text or str(exc)

    return "Duplicate entry" in text or "1062" in text


def rollback_deadlocked_transaction() -> None:
    """Reset DB connection state after MariaDB aborts a deadlocked transaction.

    InnoDB rolls back the whole transaction when it chooses it as a deadlock
    victim.  Calling Frappe's rollback here clears callbacks/savepoints and
    leaves the connection ready for a bounded idempotent retry.
    """

    frappe.db.rollback()


def first_existing_name(doctype: str, filters: dict[str, Any]) -> str | None:
    """Return the first matching document name for duplicate recovery."""

    return frappe.db.get_value(doctype, filters, "name")
