"""Canonical identifiers owned by the Media domain."""

from __future__ import annotations

import re
from typing import Any

MEDIA_ID_RE = re.compile(r"^MEDIA-[0-9a-f]{32}$")


def normalize_media_id(value: Any) -> str | None:
	"""Return a canonical Media id, or ``None`` when the value is invalid."""

	if not isinstance(value, str):
		return None
	media_id = value.strip()
	if not MEDIA_ID_RE.fullmatch(media_id):
		return None
	return media_id
