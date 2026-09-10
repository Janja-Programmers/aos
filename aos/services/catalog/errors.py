"""Public-safe Catalog exceptions with stable machine codes."""

from __future__ import annotations


class CatalogError(Exception):
    code = "CATALOG_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)


class CatalogValidationError(CatalogError, ValueError):
    code = "INVALID_CATEGORY"
    http_status = 422


class CatalogConflictError(CatalogError):
    code = "CATALOG_CONFLICT"
    http_status = 409


class CatalogNotFoundError(CatalogError, FileNotFoundError):
    code = "CATEGORY_NOT_FOUND"
    http_status = 404


class CatalogDataError(CatalogError):
    code = "CATALOG_DATA_ERROR"
    http_status = 500


_PUBLIC_MESSAGES = {
    "CATEGORY_NOT_FOUND": "Category not found.",
    "CATEGORY_NOT_SELLABLE": "Select a sellable leaf category.",
    "CATEGORY_IN_USE": "Category is still in use.",
    "ATTRIBUTE_IN_USE": "Attribute is still in use.",
    "ATTRIBUTE_IDENTITY_IMMUTABLE": "Attribute identity cannot be changed.",
    "INVALID_CATALOG_INPUT": "Invalid Catalog input.",
    "INVALID_CATEGORY": "Invalid category.",
    "INVALID_CATEGORY_IMAGE": "Invalid category image.",
    "INVALID_CATEGORY_SCHEMA": "Invalid category schema.",
    "INVALID_CATEGORY_TREE": "Invalid category hierarchy.",
    "CATALOG_CONFLICT": "Catalog was changed by another request.",
    "CATALOG_DATA_ERROR": "Catalog data is unavailable.",
}


def public_catalog_message(error: CatalogError, *, fallback: str = "Catalog request failed.") -> str:
    return _PUBLIC_MESSAGES.get(str(error.code or ""), fallback)
