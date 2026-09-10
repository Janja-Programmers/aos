from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from aos.services.media.media_service import MediaService


class TestCategoryMediaProjection(TestCase):
    def test_resource_bound_projection_rejects_stale_attachment_name(self):
        service = MediaService.__new__(MediaService)
        service._public_url_for_doc = lambda row: f"https://cdn.example.test/{row.name}.webp"
        rows = [
            SimpleNamespace(
                name="MEDIA-1",
                attached_name="Actual Category",
                bucket="public",
                object_key="catalog/categories/media-1.webp",
            )
        ]
        with patch("aos.services.media.media_service.frappe.get_all", return_value=rows) as get_all:
            result = service.get_public_attachment_url_map(
                [("MEDIA-1", "Expected Category")],
                purpose="category_icon",
                attached_doctype="AOS Category",
                attached_field="image_media",
            )

        self.assertEqual(result, {})
        filters = get_all.call_args.kwargs["filters"]
        self.assertEqual(filters["purpose"], "category_icon")
        self.assertEqual(filters["status"], "Attached")
        self.assertEqual(filters["attached_doctype"], "AOS Category")
        self.assertEqual(filters["attached_field"], "image_media")

    def test_resource_bound_projection_returns_only_exact_expected_pairs(self):
        service = MediaService.__new__(MediaService)
        service._public_url_for_doc = lambda row: f"https://cdn.example.test/{row.name}.webp"
        rows = [
            SimpleNamespace(name="MEDIA-1", attached_name="Category A", bucket="public", object_key="a"),
            SimpleNamespace(name="MEDIA-2", attached_name="Category B", bucket="public", object_key="b"),
        ]
        with patch("aos.services.media.media_service.frappe.get_all", return_value=rows):
            result = service.get_public_attachment_url_map(
                [("MEDIA-1", "Category A"), ("MEDIA-2", "Other")],
                purpose="category_icon",
                attached_doctype="AOS Category",
                attached_field="image_media",
            )
        self.assertEqual(
            result,
            {("MEDIA-1", "Category A"): "https://cdn.example.test/MEDIA-1.webp"},
        )
