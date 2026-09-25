"""Opaque Activity Center identifiers."""

from __future__ import annotations

import re
from typing import Any

from aos.utils.identifiers import new_prefixed_name

from .constants import ACTIVITY_PUBLIC_ID_PREFIX

_ACTIVITY_ID_RE = re.compile(r"^ACT-[0-9a-f]{32}$")


def new_activity_id() -> str:
    return new_prefixed_name(ACTIVITY_PUBLIC_ID_PREFIX)


def normalize_activity_id(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if _ACTIVITY_ID_RE.fullmatch(candidate) else ""
