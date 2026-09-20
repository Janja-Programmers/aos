"""Opaque public identifiers for Shorts domain resources."""
from __future__ import annotations

import base64
import re
import secrets
from typing import Any

SHORT_ID_RE = re.compile(r"^SHR-[A-Z2-7]{20}$")
SOUND_ID_RE = re.compile(r"^SND-[A-Z2-7]{20}$")
PROCESSING_JOB_ID_RE = re.compile(r"^VPJ-[A-Z2-7]{20}$")
COMMENT_ID_RE = re.compile(r"^SHC-[A-Z2-7]{20}$")


def _token() -> str:
    return base64.b32encode(secrets.token_bytes(12)).decode("ascii").rstrip("=")


def generate_short_id() -> str:
    return f"SHR-{_token()}"


def generate_sound_id() -> str:
    return f"SND-{_token()}"


def generate_processing_job_id() -> str:
    return f"VPJ-{_token()}"


def generate_comment_id() -> str:
    return f"SHC-{_token()}"


def normalize_short_id(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if SHORT_ID_RE.fullmatch(candidate) else ""


def normalize_sound_id(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if SOUND_ID_RE.fullmatch(candidate) else ""
