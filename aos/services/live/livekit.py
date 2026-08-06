"""Privacy-safe LiveKit identity and metadata helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import frappe

from aos.services.accounts.identity import public_account_id_for_user


def _secret() -> bytes:
    value = ""
    try:
        value = str(getattr(frappe.local, "conf", {}).get("encryption_key") or "")
    except Exception:
        pass
    if not value:
        try:
            value = str((frappe.get_site_config() or {}).get("encryption_key") or "")
        except Exception:
            pass
    if not value:
        raise RuntimeError("Live identity signing key is unavailable.")
    return value.encode("utf-8")


def _opaque(parts: list[str]) -> str:
    digest = hmac.new(_secret(), "|".join(parts).encode("utf-8"), hashlib.sha256).digest()[:18]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def participant_identity(*, live_id: str, role: str, user: str | None, session_id: str | None) -> str:
    public_account = public_account_id_for_user(user) if user else "guest"
    session = str(session_id or "host").strip()
    scope = "host" if str(role).lower() == "host" else "participant"
    return f"aos:{scope}:{_opaque([str(live_id), str(public_account), session])}"


def participant_metadata(
    *,
    role: str,
    user: str | None,
    display_name: str | None,
    avatar: str | None,
    is_guest: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": public_account_id_for_user(user) if user else None,
        "role": str(role).lower(),
        "display_name": str(display_name or "")[:120] or None,
        "avatar": str(avatar or "")[:500] or None,
        "is_guest": bool(is_guest),
    }
    for key, value in dict(extra or {}).items():
        if key in {"cohost_id"}:
            payload[key] = value
    return {key: value for key, value in payload.items() if value is not None}
