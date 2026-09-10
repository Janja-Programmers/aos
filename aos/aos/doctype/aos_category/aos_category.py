# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document

from aos.services.catalog.cache import clear_catalog_cache
from aos.services.catalog.category_media import (
    finalize_category_image,
    prepare_category_image,
    release_category_image_on_delete,
)
from aos.services.catalog.integrity import (
    assert_category_delete_safe,
    assert_category_schema_change_safe,
    assert_category_transition_safe,
    lock_category_mutation,
)
from aos.services.catalog.observability import catalog_log
from aos.services.catalog.validation import validate_category_document


class AOSCategory(Document):
    """Canonical two-level marketplace category and its Media relationship."""

    def validate(self):
        try:
            lock_category_mutation(self)
            validate_category_document(self)
            assert_category_transition_safe(self)
            assert_category_schema_change_safe(self)
            prepare_category_image(self)
        except Exception:
            catalog_log("configuration_rejected", outcome="rejected")
            raise

    def on_update(self):
        finalize_category_image(self)
        clear_catalog_cache()
        catalog_log(
            "configuration_changed",
            outcome="success",
            category_kind="group" if int(self.is_group or 0) else "leaf",
        )

    def on_trash(self):
        lock_category_mutation(self)
        assert_category_delete_safe(self.name)
        release_category_image_on_delete(self)
        clear_catalog_cache()
