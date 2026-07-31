"""Canonical Shorts domain package.

The public API remains under :mod:`aos.api.v1.shorts`.  This package owns
cross-endpoint policy, validation, persistence helpers, media rules, processing
state transitions, and privacy-safe observability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import ShortsService

__all__ = ["ShortsService"]


def __getattr__(name: str) -> Any:
    if name == "ShortsService":
        from .service import ShortsService

        return ShortsService
    raise AttributeError(name)
