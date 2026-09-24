"""Per-user Chat Lock and secret-code access.

Chat Lock is application privacy, not end-to-end encryption. A secret is slow-
hashed and never stored or logged. Hidden locked chats require a short-lived
server token after successful secret verification; tokens live only in shared
Redis so all app nodes enforce the same state.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
from typing import Any

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils.password import passlibctx

from aos.services.chat.errors import ChatError
from aos.services.chat.membership import get_membership

MIN_SECRET_LENGTH = 4
MAX_SECRET_LENGTH = 64
MAX_SECRET_ATTEMPTS = 5
SECRET_LOCKOUT_MINUTES = 15
ACCESS_TOKEN_TTL_SECONDS = 300


def _normalize_secret(value: Any) -> str:
    if not isinstance(value, str):
        raise ChatError("Invalid Chat Lock secret.", code="CHAT_LOCK_INVALID_SECRET", http_status=422)
    secret = unicodedata.normalize("NFC", value).strip()
    if len(secret) < MIN_SECRET_LENGTH or len(secret) > MAX_SECRET_LENGTH or "\x00" in secret:
        raise ChatError("Invalid Chat Lock secret.", code="CHAT_LOCK_INVALID_SECRET", http_status=422)
    return secret


def _credential_for_user(user: str, *, for_update: bool = False):
    sql = """
        SELECT name, user, secret_hash, secret_version, hide_locked_chats,
               failed_attempts, locked_until, last_verified_at
        FROM `tabAOS Chat Lock Credential`
        WHERE user=%s
        LIMIT 1
    """
    if for_update:
        sql += " FOR UPDATE"
    rows = frappe.db.sql(sql, (user,), as_dict=True)
    return rows[0] if rows else None



def _lock_user_row(user: str) -> None:
    """Serialize credential creation/update when no credential row exists yet."""
    rows = frappe.db.sql("SELECT name FROM `tabUser` WHERE name=%s LIMIT 1 FOR UPDATE", (user,), pluck=True)
    if not rows:
        raise ChatError("Account not found.", code="CHAT_NOT_FOUND", http_status=404)


def _cache_key(*, user: str, token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user_digest = hashlib.sha256(user.encode("utf-8")).hexdigest()[:24]
    return f"aos:chat-lock:v1:{user_digest}:{digest}"


def _cache():
    return frappe.cache()


def set_secret(*, user: str, secret: str, current_secret: str | None, hide_locked_chats: bool) -> dict[str, Any]:
    clean = _normalize_secret(secret)
    _lock_user_row(user)
    row = _credential_for_user(user, for_update=True)
    now = now_datetime()
    if row:
        if not current_secret:
            raise ChatError("Current Chat Lock secret is required.", code="CHAT_LOCK_SECRET_REQUIRED", http_status=403)
        _verify_row_secret(row, current_secret, now=now, mutate_failures=True)
        version = int(row.secret_version or 1) + 1
        frappe.db.set_value(
            "AOS Chat Lock Credential",
            row.name,
            {
                "secret_hash": passlibctx.hash(clean),
                "secret_version": version,
                "hide_locked_chats": 1 if hide_locked_chats else 0,
                "failed_attempts": 0,
                "locked_until": None,
                "last_verified_at": now,
            },
            update_modified=True,
        )
    else:
        doc = frappe.new_doc("AOS Chat Lock Credential")
        doc.user = user
        doc.secret_hash = passlibctx.hash(clean)
        doc.secret_version = 1
        doc.hide_locked_chats = 1 if hide_locked_chats else 0
        doc.failed_attempts = 0
        doc.insert(ignore_permissions=True)
        version = 1
    return {"configured": True, "hide_locked_chats": bool(hide_locked_chats), "secret_version": version}


def remove_secret(*, user: str, current_secret: str) -> dict[str, Any]:
    _lock_user_row(user)
    row = _credential_for_user(user, for_update=True)
    if not row:
        return {"configured": False, "hide_locked_chats": False}
    _verify_row_secret(row, current_secret, now=now_datetime(), mutate_failures=True)
    frappe.delete_doc("AOS Chat Lock Credential", row.name, ignore_permissions=True, force=True)
    return {"configured": False, "hide_locked_chats": False}


def _verify_row_secret(row, candidate: str, *, now, mutate_failures: bool) -> None:
    if row.locked_until and row.locked_until > now:
        raise ChatError("Chat Lock secret is temporarily locked.", code="CHAT_LOCK_RATE_LIMITED", http_status=429)
    clean = _normalize_secret(candidate)
    try:
        valid = bool(passlibctx.verify(clean, str(row.secret_hash or "")))
    except Exception:
        valid = False
    if not valid:
        if mutate_failures:
            attempts = int(row.failed_attempts or 0) + 1
            updates: dict[str, Any] = {"failed_attempts": attempts}
            if attempts >= MAX_SECRET_ATTEMPTS:
                updates["failed_attempts"] = 0
                updates["locked_until"] = add_to_date(now, minutes=SECRET_LOCKOUT_MINUTES)
            frappe.db.set_value("AOS Chat Lock Credential", row.name, updates, update_modified=True)
        raise ChatError("Invalid Chat Lock secret.", code="CHAT_LOCK_INVALID_SECRET", http_status=403)
    if mutate_failures:
        frappe.db.set_value(
            "AOS Chat Lock Credential",
            row.name,
            {"failed_attempts": 0, "locked_until": None, "last_verified_at": now},
            update_modified=True,
        )


def verify_secret(*, user: str, secret: str) -> dict[str, Any]:
    row = _credential_for_user(user, for_update=True)
    if not row:
        raise ChatError("Chat Lock secret is not configured.", code="CHAT_LOCK_SECRET_REQUIRED", http_status=409)
    _verify_row_secret(row, secret, now=now_datetime(), mutate_failures=True)
    token = secrets.token_urlsafe(32)
    _cache().set_value(
        _cache_key(user=user, token=token),
        str(int(row.secret_version or 1)),
        expires_in_sec=ACCESS_TOKEN_TTL_SECONDS,
    )
    return {"lock_token": token, "expires_in": ACCESS_TOKEN_TTL_SECONDS}


def credential_state(user: str) -> dict[str, Any]:
    row = _credential_for_user(user)
    return {
        "secret_configured": bool(row),
        "hide_locked_chats": bool(int(row.hide_locked_chats or 0)) if row else False,
    }


def token_valid(*, user: str, token: str | None) -> bool:
    row = _credential_for_user(user)
    if not row or not token:
        return False
    try:
        cached = _cache().get_value(_cache_key(user=user, token=str(token)))
    except Exception:
        return False
    if isinstance(cached, bytes):
        cached = cached.decode("utf-8", errors="ignore")
    return str(cached or "") == str(int(row.secret_version or 1))


def hidden_lock_requires_token(*, user: str, membership: Any) -> bool:
    if not membership or not bool(int(membership.is_locked or 0)):
        return False
    row = _credential_for_user(user)
    return bool(row and int(row.hide_locked_chats or 0))


def authorize_locked_conversation(*, user: str, conversation_id: str, lock_token: str | None) -> None:
    membership = get_membership(conversation_id, user, include_inactive=False)
    if not membership:
        return
    if hidden_lock_requires_token(user=user, membership=membership) and not token_valid(user=user, token=lock_token):
        # Deliberately indistinguishable from unknown conversation when hidden.
        raise ChatError("Conversation not found.", code="CHAT_NOT_FOUND", http_status=404)
