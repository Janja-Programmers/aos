from __future__ import annotations

from unittest.mock import Mock, patch

from frappe.tests.utils import FrappeTestCase

from aos.integrations.ai.image_search_client import (
    ImageSearchClient,
    ImageSearchClientSettings,
    ImageSearchUnavailableError,
    _internal_signature,
)


class _Response:
    status_code = 200

    def json(self):
        return {
            "ok": True,
            "ad_id": "AD-1",
            "indexed_count": 1,
            "failed_count": 0,
        }


class TestImageSearchClientSecurity(FrappeTestCase):
    def _settings(self, *, secret: str = "image-search-secret-value-0123456789abcdef"):
        return ImageSearchClientSettings(
            service_url="http://127.0.0.1:8110",
            timeout_seconds=20,
            default_limit=20,
            max_limit=100,
            internal_secret=secret,
        )

    def test_internal_mutation_is_signed_over_exact_method_path_and_body(self):
        session = Mock()
        session.request.return_value = _Response()
        client = ImageSearchClient(settings=self._settings(), session=session)

        with patch("aos.integrations.ai.image_search_client.time.time", return_value=1700000000):
            result = client.replace_ad_images(
                ad_id="AD-1",
                images=[{"image_url": "https://files.example.test/ad.jpg", "sort_order": 0}],
            )

        self.assertTrue(result["ok"])
        call = session.request.call_args
        body = call.kwargs["data"]
        headers = call.kwargs["headers"]
        timestamp = headers["X-AOS-Timestamp"]
        self.assertEqual(timestamp, "1700000000")
        self.assertEqual(
            headers["X-AOS-Signature"],
            _internal_signature(
                self._settings().internal_secret,
                timestamp=timestamp,
                method="POST",
                path="/ads/AD-1/replace-images",
                body=body,
            ),
        )

    def test_internal_mutation_fails_closed_when_secret_is_missing(self):
        session = Mock()
        client = ImageSearchClient(settings=self._settings(secret=""), session=session)

        with self.assertRaises(ImageSearchUnavailableError):
            client.delete_ad_vectors(ad_id="AD-1")

        session.request.assert_not_called()
