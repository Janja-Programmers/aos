from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.tests.utils import FrappeTestCase

from aos.services.catalog.category_media import (
    finalize_category_image,
    prepare_category_image,
    release_category_image_on_delete,
)
from aos.services.media.media_service import MediaConflictError, MediaNotFoundError


class FakeCategory(SimpleNamespace):
    def is_new(self):
        return bool(getattr(self, "_new", False))

    def get_doc_before_save(self):
        return getattr(self, "_before", None)


class TestCategoryMediaHooks(FrappeTestCase):
    def test_new_category_image_is_validated_through_media_policy(self):
        category = FakeCategory(
            _new=False,
            image_media="MEDIA-NEW",
            doctype="AOS Category",
            name="Category",
            _before=SimpleNamespace(image_media="MEDIA-OLD"),
        )
        with (
            patch("aos.services.catalog.category_media.MediaService") as service_factory,
            patch("aos.services.catalog.category_media.frappe.session") as session,
        ):
            session.user = "Administrator"
            prepare_category_image(category)

        service_factory.return_value.validate_media_for_use.assert_called_once_with(
            media_id="MEDIA-NEW",
            user="Administrator",
            purpose="category_icon",
            attached_doctype="AOS Category",
            attached_name="Category",
        )
        self.assertEqual(category._previous_image_media_id, "MEDIA-OLD")

    def test_replacement_attaches_new_before_releasing_old(self):
        category = FakeCategory(
            image_media="MEDIA-NEW",
            _previous_image_media_id="MEDIA-OLD",
            doctype="AOS Category",
            name="Category",
        )
        service = Mock()
        sequence = []
        service.attach_media.side_effect = lambda **_kwargs: sequence.append("attach")
        service.release_media.side_effect = lambda **_kwargs: sequence.append("release")
        with (
            patch("aos.services.catalog.category_media.MediaService", return_value=service),
            patch("aos.services.catalog.category_media.frappe.session") as session,
        ):
            session.user = "Administrator"
            finalize_category_image(category)

        self.assertEqual(sequence, ["attach", "release"])
        service.attach_media.assert_called_once_with(
            media_id="MEDIA-NEW",
            user="Administrator",
            purpose="category_icon",
            attached_doctype="AOS Category",
            attached_name="Category",
            attached_field="image_media",
            replacing_media_id="MEDIA-OLD",
        )
        service.release_media.assert_called_once_with(
            media_id="MEDIA-OLD",
            user="Administrator",
            attached_doctype="AOS Category",
            attached_name="Category",
            replacement_media_id="MEDIA-NEW",
            system=True,
        )

    def test_unchanged_image_is_lifecycle_noop(self):
        category = FakeCategory(
            image_media="MEDIA-SAME",
            _previous_image_media_id="MEDIA-SAME",
            doctype="AOS Category",
            name="Category",
        )
        with patch("aos.services.catalog.category_media.MediaService") as service_factory:
            finalize_category_image(category)
        service_factory.assert_not_called()

    def test_stale_old_media_does_not_block_repair(self):
        category = FakeCategory(
            image_media="MEDIA-NEW",
            _previous_image_media_id="MEDIA-MISSING",
            doctype="AOS Category",
            name="Category",
        )
        service = Mock()
        service.release_media.side_effect = MediaNotFoundError("missing")
        with (
            patch("aos.services.catalog.category_media.MediaService", return_value=service),
            patch("aos.services.catalog.category_media.frappe.session") as session,
            patch("aos.services.catalog.category_media.frappe.logger"),
        ):
            session.user = "Administrator"
            finalize_category_image(category)
        service.attach_media.assert_called_once()
        service.release_media.assert_called_once()

    def test_stale_media_attached_elsewhere_is_not_mutated(self):
        category = FakeCategory(
            image_media="",
            doctype="AOS Category",
            name="Category",
        )
        service = Mock()
        service.release_media.side_effect = MediaConflictError("different resource")
        with (
            patch("aos.services.catalog.category_media.MediaService", return_value=service),
            patch("aos.services.catalog.category_media.frappe.session") as session,
            patch("aos.services.catalog.category_media.frappe.logger"),
        ):
            session.user = "Administrator"
            category.image_media = "MEDIA-STALE"
            release_category_image_on_delete(category)
        service.release_media.assert_called_once()

    def test_empty_category_image_is_delete_noop(self):
        category = FakeCategory(image_media="", doctype="AOS Category", name="Category")
        with patch("aos.services.catalog.category_media.MediaService") as service_factory:
            release_category_image_on_delete(category)
        service_factory.assert_not_called()
