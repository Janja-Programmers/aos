"""Auth-owned revocation of sessions and authentication-related tokens."""

from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe.utils import now_datetime


class SessionRevocationError(RuntimeError):
    pass


_MISSING_SESSION_CREATION_FLAG = object()


@contextmanager
def aos_session_creation_scope():
    """Temporarily authorize Frappe session creation for the AOS auth path.

    The marker is request-local and must restore the exact previous state so
    nested calls/tests cannot accidentally leave generic Frappe login enabled.
    """
    previous = getattr(frappe.flags, "aos_auth_session_creation", _MISSING_SESSION_CREATION_FLAG)
    frappe.flags.aos_auth_session_creation = True
    try:
        yield
    finally:
        if previous is _MISSING_SESSION_CREATION_FLAG:
            try:
                frappe.flags.pop("aos_auth_session_creation", None)
            except Exception:
                frappe.flags.aos_auth_session_creation = False
        else:
            frappe.flags.aos_auth_session_creation = previous


def _clear_session_cache(sids: tuple[str, ...]) -> None:
    cache = frappe.cache()
    for sid in sids:
        cache.hdel("session", sid)


def _after_commit(callback) -> None:
    # Frappe 17 transaction callbacks are authoritative for cache invalidation.
    # Do not invalidate session cache before the DB transaction commits.
    frappe.db.after_commit.add(callback)


def revoke_all_sessions(user: str, *, keep_sid: str | None = None) -> int:
    """Delete exact session rows without committing and invalidate exact Redis keys."""
    if not user:
        return 0
    try:
        params: list[str] = [user]
        where = "user = %s"
        if keep_sid:
            where += " AND sid != %s"
            params.append(keep_sid)
        rows = frappe.db.sql(f"SELECT sid FROM `tabSessions` WHERE {where}", tuple(params), as_dict=True)
        sids = tuple(str(row.get("sid") or "") for row in rows if row.get("sid"))
        if sids:
            placeholders = ",".join(["%s"] * len(sids))
            frappe.db.sql(f"DELETE FROM `tabSessions` WHERE sid IN ({placeholders})", sids)
            _after_commit(lambda: _clear_session_cache(sids))
        return len(sids)
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
    if not frappe.db.exists("DocType", "AOS Auth Challenge"):
        return 0
    try:
        rows = frappe.db.sql(
            "SELECT COUNT(*) AS count FROM `tabAOS Auth Challenge` WHERE user = %s AND is_used = 0",
            (user,),
            as_dict=True,
        )
        count = int(rows[0].get("count") or 0) if rows else 0
        frappe.db.sql(
            """
            UPDATE `tabAOS Auth Challenge`
            SET is_used = 1, otp_password_hash = '', continuation_token_hash = '', continuation_expires_at = NULL
            WHERE user = %s
            """,
            (user,),
        )
        return count
    except Exception as exc:
        raise SessionRevocationError("Unable to revoke verification tokens") from exc


def revoke_account_access(user: str) -> dict[str, int]:
    return {
        "sessions_revoked": revoke_all_sessions(user),
        "push_tokens_revoked": revoke_push_tokens(user),
        "verification_tokens_revoked": revoke_verification_tokens(user),
    }
