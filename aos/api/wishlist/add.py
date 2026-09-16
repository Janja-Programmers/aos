"""Explicit idempotent Wishlist add operation."""

from __future__ import annotations

from aos.services.wishlist.service import WishlistService

from .mutation import run_wishlist_mutation


def add_to_wishlist_impl(**kwargs):
    return run_wishlist_mutation(
        kwargs=kwargs,
        action="add",
        mutate=lambda service, user, public_ad_id: service.add(
            user=user,
            public_ad_id=public_ad_id,
        ),
    )
