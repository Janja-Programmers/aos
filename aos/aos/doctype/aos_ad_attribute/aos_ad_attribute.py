# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document

from aos.services.catalog.observability import catalog_log
from aos.services.catalog.service import CatalogService
from aos.services.catalog.validation import validate_attribute_document


class AOSAdAttribute(Document):
    """Administrator-managed Catalog attribute definition."""

    def validate(self):
        try:
            validate_attribute_document(self)
        except Exception:
            catalog_log("configuration_rejected", outcome="rejected")
            raise

    def on_update(self):
        CatalogService.invalidate_cache()
        catalog_log("configuration_changed", outcome="success")

    def on_trash(self):
        CatalogService.invalidate_cache()
