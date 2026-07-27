"""Wishlist API limits.

The values live in the domain package so API, tests, and operations share one
source of truth. These aliases preserve the existing import path.
"""

from aos.services.wishlist.constants import (
    WISHLIST_LIST_LIMIT_PER_MINUTE_PER_USER,
    WISHLIST_TOGGLE_LIMIT_PER_MINUTE_PER_USER,
)

__all__ = [
    "WISHLIST_LIST_LIMIT_PER_MINUTE_PER_USER",
    "WISHLIST_TOGGLE_LIMIT_PER_MINUTE_PER_USER",
]
