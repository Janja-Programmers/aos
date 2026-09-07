"""Accounts lifecycle background jobs."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.services.account_purge_service import purge_expired_deleted_account
from aos.services.accounts.constants import (
    ACCOUNT_PURGE_SCAN_LIMIT,
    ACCOUNT_STATUS_DELETED,
    PURGE_STATUS_COMPLETED,
)


def purge_expired_deleted_accounts() -> dict[str, int]:
    """Advance permanent deletion for a bounded set of expired accounts."""
    rows = frappe.db.sql(
        """
        SELECT user
        FROM `tabAOS Profile`
        WHERE account_status = %(deleted)s
          AND restore_deadline IS NOT NULL
          AND restore_deadline < %(now)s
          AND COALESCE(purge_status, 'Pending') != %(completed)s
        ORDER BY restore_deadline ASC, user ASC
        LIMIT %(limit)s
        """,
        {
            "deleted": ACCOUNT_STATUS_DELETED,
            "now": now_datetime(),
            "completed": PURGE_STATUS_COMPLETED,
            "limit": ACCOUNT_PURGE_SCAN_LIMIT,
        },
        as_dict=True,
    )
    completed = 0
    progressed = 0
    for index, row in enumerate(rows):
        user = str(row.user or "").strip()
        if not user:
            continue
        savepoint = f"account_purge_{index}"
        frappe.db.savepoint(savepoint)
        try:
            result = purge_expired_deleted_account(user)
            progressed += 1
            completed += int(bool(result.get("completed")))
        except Exception as exc:
            # Isolate one account's failure without committing partial cleanup
            # for that account or blocking the remaining bounded scan.
            frappe.db.rollback(save_point=savepoint)
            from aos.services.accounts.observability import account_log

            account_log(
                "account.permanent_deletion.progress",
                user=user,
                outcome="failure",
                failure_category=exc.__class__.__name__,
            )
    return {"scanned": len(rows), "progressed": progressed, "completed": completed}
