# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document

from aos.services.catalog.cache import clear_catalog_cache
from aos.services.catalog.integrity import (
    assert_attribute_delete_safe,
    assert_attribute_identity_immutable,
    assert_attribute_schema_change_safe,
    lock_attribute_mutation,
)
from aos.services.catalog.observability import catalog_log
from aos.services.catalog.validation import validate_attribute_document


class AOSAdAttribute(Document):
    """Administrator-managed reusable Catalog attribute definition."""

    def validate(self):
        try:
            lock_attribute_mutation(self)
            validate_attribute_document(self)
            assert_attribute_identity_immutable(self)
            assert_attribute_schema_change_safe(self)
        except Exception:
            catalog_log("configuration_rejected", outcome="rejected")
            raise

    def on_update(self):
        clear_catalog_cache()
        catalog_log("configuration_changed", outcome="success")

    def on_trash(self):
        lock_attribute_mutation(self)
        assert_attribute_delete_safe(self.name)
        clear_catalog_cache()
