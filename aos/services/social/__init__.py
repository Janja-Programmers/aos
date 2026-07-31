"""Canonical Social domain package.

The package deliberately avoids eager service imports. Importing a concrete
submodule such as :mod:`aos.services.social.repository` must not initialize the
service layer because the service depends on shared account serializers that
also use the repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import SocialService as SocialService

__all__ = ["SocialService"]


def __getattr__(name: str) -> Any:
    """Load the public service lazily without creating package import cycles."""
    if name == "SocialService":
        from .service import SocialService

        return SocialService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
