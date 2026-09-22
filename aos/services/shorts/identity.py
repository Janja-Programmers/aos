"""Opaque public identifiers for Shorts domain resources."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
from typing import Any

SHORT_ID_RE = re.compile(r"^SHR-[A-Z2-7]{20}$")
SOUND_ID_RE = re.compile(r"^SND-[A-Z2-7]{20}$")
COMMENT_ID_RE = re.compile(r"^SHC-[A-Z2-7]{20}$")


def _token() -> str:
    return base64.b32encode(secrets.token_bytes(12)).decode("ascii").rstrip("=")


def generate_short_id() -> str:
    return f"SHR-{_token()}"


def generate_sound_id() -> str:
    return f"SND-{_token()}"


def generate_comment_id() -> str:
    return f"SHC-{_token()}"


def normalize_short_id(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if SHORT_ID_RE.fullmatch(candidate) else ""


def normalize_sound_id(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if SOUND_ID_RE.fullmatch(candidate) else ""


def short_view_identity_key(*, short_id: Any, user: Any = None, session_id: Any = None) -> str:
    """Return the canonical one-view-per-actor identity for a Short."""
    sid = str(short_id or "").strip()
    clean_user = str(user or "").strip()
    clean_session = str(session_id or "").strip()
    if not sid or (not clean_user and not clean_session):
        return ""
    actor_key = f"user:{clean_user}" if clean_user else f"session:{clean_session}"
    return hashlib.sha256(f"{sid}|{actor_key}".encode()).hexdigest()
