"""Auth-owned revocation of sessions and authentication-related tokens."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime


class SessionRevocationError(RuntimeError):
    pass


def revoke_all_sessions(user: str) -> int:
    """Delete all server sessions for one internal User.name without committing."""
    if not user:
        return 0
    try:
        rows = frappe.db.sql("SELECT COUNT(*) AS count FROM `tabSessions` WHERE user = %s", (user,), as_dict=True)
        count = int(rows[0].get("count") or 0) if rows else 0
        frappe.db.sql("DELETE FROM `tabSessions` WHERE user = %s", (user,))
        try:
            frappe.cache().delete_keys(f"*{user}*")
        except Exception:
            pass
        return count
    except Exception as exc:
        raise SessionRevocationError("Unable to revoke account sessions") from exc


def revoke_push_tokens(user: str) -> int:
    if not frappe.db.exists("DocType", "AOS Push Token"):
        return 0
    try:
        rows = frappe.db.sql(
            "SELECT COUNT(*) AS count FROM `tabAOS Push Token` WHERE user = %s AND is_active = 1",
            (user,),
            as_dict=True,
        )
        count = int(rows[0].get("count") or 0) if rows else 0
        frappe.db.sql(
            """
            UPDATE `tabAOS Push Token`
            SET is_active = 0, active_device_key = NULL, last_used_at = %s
            WHERE user = %s
            """,
            (now_datetime(), user),
        )
        return count
    except Exception as exc:
        raise SessionRevocationError("Unable to revoke push tokens") from exc


def revoke_verification_tokens(user: str) -> int:
    if not frappe.db.exists("DocType", "AOS Email Verification"):
        return 0
    try:
        rows = frappe.db.sql(
            "SELECT COUNT(*) AS count FROM `tabAOS Email Verification` WHERE user = %s AND is_used = 0",
            (user,),
            as_dict=True,
        )
        count = int(rows[0].get("count") or 0) if rows else 0
        frappe.db.sql(
            """
            UPDATE `tabAOS Email Verification`
            SET is_used = 1, reset_token_hash = '', reset_token_expires_at = NULL
            WHERE user = %s
            """,
            (user,),
        )
        return count
    except Exception as exc:
        raise SessionRevocationError("Unable to revoke verification tokens") from exc


def revoke_account_access(user: str) -> dict[str, int]:
    """Fail closed: lifecycle transitions must not succeed with live access."""
    return {
        "sessions_revoked": revoke_all_sessions(user),
        "push_tokens_revoked": revoke_push_tokens(user),
        "verification_tokens_revoked": revoke_verification_tokens(user),
    }
