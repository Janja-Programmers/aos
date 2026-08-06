"""Canonical AOS Live domain helpers.

The package deliberately avoids eager imports so Live serializers, Social,
Accounts and notification modules can depend on one another without cycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .errors import LiveError as LiveError

__all__ = ["LiveError"]


def __getattr__(name: str) -> Any:
    if name == "LiveError":
        from .errors import LiveError

        return LiveError
    raise AttributeError(name)
