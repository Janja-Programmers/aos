"""Catalog category filter expansion for buyer listing queries."""

from __future__ import annotations

from typing import List

from aos.services.catalog.errors import CatalogError
from aos.services.catalog.service import CatalogService


def resolve_category_filter_values(category: str) -> List[str]:
    """Resolve a public leaf to itself or a public group to active leaf children."""

    try:
        return CatalogService().resolve_filter_values(category)
    except CatalogError:
        return []
