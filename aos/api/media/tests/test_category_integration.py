from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from aos.aos.doctype.aos_category.aos_category import AOSCategory


class TestCategoryMediaHooks(FrappeTestCase):
    def test_empty_icon_relationship_is_install_safe_noop(self):
        category = SimpleNamespace(icon_media="", _previous_icon_media_id="")

        with patch("aos.aos.doctype.aos_category.aos_category.MediaService") as service_factory:
            AOSCategory._finalize_icon_media_relationship(category)

        service_factory.assert_not_called()


    def test_category_icon_replacement_uses_shared_media_lifecycle(self):
        category = SimpleNamespace(
            icon_media="MEDIA-NEW",
            _previous_icon_media_id="MEDIA-OLD",
            doctype="AOS Category",
            name="Category",
        )
        with (
            patch("aos.aos.doctype.aos_category.aos_category.MediaService") as service_factory,
            patch("aos.aos.doctype.aos_category.aos_category.frappe.session") as session,
        ):
            session.user = "Administrator"
            AOSCategory._finalize_icon_media_relationship(category)

        service = service_factory.return_value
        service.attach_media.assert_called_once_with(
            media_id="MEDIA-NEW",
            user="Administrator",
            purpose="category_icon",
            attached_doctype="AOS Category",
            attached_name="Category",
            attached_field="icon_media",
            replacing_media_id="MEDIA-OLD",
        )
        service.release_media.assert_called_once_with(
            media_id="MEDIA-OLD",
            user="Administrator",
            attached_doctype="AOS Category",
            attached_name="Category",
            replacement_media_id="MEDIA-NEW",
        )

    def test_duplicate_category_icon_attachment_does_not_release_current_media(self):
        category = SimpleNamespace(
            icon_media="MEDIA-SAME",
            _previous_icon_media_id="MEDIA-SAME",
            doctype="AOS Category",
            name="Category",
        )
        with (
            patch("aos.aos.doctype.aos_category.aos_category.MediaService") as service_factory,
            patch("aos.aos.doctype.aos_category.aos_category.frappe.session") as session,
        ):
            session.user = "Administrator"
            AOSCategory._finalize_icon_media_relationship(category)

        service = service_factory.return_value
        service.attach_media.assert_called_once()
        service.release_media.assert_not_called()

    def test_category_delete_releases_attached_icon(self):
        category = SimpleNamespace(
            icon_media="MEDIA-CATEGORY-1",
            doctype="AOS Category",
            name="Category",
        )
        with (
            patch("aos.aos.doctype.aos_category.aos_category.MediaService") as service_factory,
            patch("aos.aos.doctype.aos_category.aos_category.CatalogService.invalidate_cache"),
            patch("aos.aos.doctype.aos_category.aos_category.frappe.session") as session,
        ):
            session.user = "Administrator"
            AOSCategory.on_trash(category)
        service_factory.return_value.release_media.assert_called_once_with(
            media_id="MEDIA-CATEGORY-1",
            user="Administrator",
            attached_doctype="AOS Category",
            attached_name="Category",
            replacement_media_id=None,
        )

    def test_category_delete_without_icon_is_storage_noop(self):
        category = SimpleNamespace(icon_media="", doctype="AOS Category", name="Category")
        with (
            patch("aos.aos.doctype.aos_category.aos_category.MediaService") as service_factory,
            patch("aos.aos.doctype.aos_category.aos_category.CatalogService.invalidate_cache"),
        ):
            AOSCategory.on_trash(category)
        service_factory.assert_not_called()
