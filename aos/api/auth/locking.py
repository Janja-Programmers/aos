"""Cross-node Authentication coordination using database row locks."""

from __future__ import annotations

import frappe


def lock_user(user: str) -> bool:
    """Lock one User row until the surrounding DB transaction completes.

    This is deliberately account-scoped. It orders session creation against
    password/account mutations without requiring process-local or global locks.
    """
    if not user:
        return False
    rows = frappe.db.sql(
        "SELECT name FROM `tabUser` WHERE name = %s LIMIT 1 FOR UPDATE",
        (user,),
    )
    return bool(rows)
