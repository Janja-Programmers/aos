"""Catalog domain services.

The current AOS Catalog domain is marketplace taxonomy metadata: categories,
category attributes, pricing rules, and category icon media. Sellable inventory
continues to be represented by ``AOS Ad``; this package intentionally does not
invent a separate product or SKU aggregate.
"""

from .service import CatalogService

__all__ = ["CatalogService"]
