"""Canonical Shorts service façade.

Endpoint modules remain compatibility adapters while cross-feature callers can
use this façade without importing public API modules.
"""

from __future__ import annotations

from typing import Any

from .policy import can_comment, can_download, can_view
from .repository import ShortsRepository


class ShortsService:
    def __init__(self, repository: ShortsRepository | None = None) -> None:
        self.repository = repository or ShortsRepository()

    def can_view(self, short: Any, *, viewer: str | None) -> bool:
        return can_view(short, viewer=viewer)

    def can_comment(self, short: Any, *, viewer: str | None) -> bool:
        return can_comment(short, viewer=viewer)

    def can_download(self, short: Any, *, viewer: str | None) -> bool:
        return can_download(short, viewer=viewer)
