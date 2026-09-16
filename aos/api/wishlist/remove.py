"""Explicit idempotent Wishlist remove operation."""

from __future__ import annotations

from .mutation import run_wishlist_mutation


def remove_from_wishlist_impl(**kwargs):
    return run_wishlist_mutation(
        kwargs=kwargs,
        action="remove",
        mutate=lambda service, user, public_ad_id: service.remove(
            user=user,
            public_ad_id=public_ad_id,
        ),
    )
