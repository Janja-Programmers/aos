from __future__ import annotations

from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase

from aos.integrations.ai.background_removal_client import _is_png_response


class TestBackgroundRemovalClientResponseValidation(FrappeTestCase):
    PNG = b"\x89PNG\r\n\x1a\nsynthetic"

    @staticmethod
    def _response(*, content_type: str = "image/png", output_format: str = "png"):
        return SimpleNamespace(
            headers={
                "Content-Type": content_type,
                "X-AOS-Output-Format": output_format,
            }
        )

    def test_accepts_canonical_png_processor_response(self):
        self.assertTrue(_is_png_response(self._response(), self.PNG))

    def test_rejects_non_png_payload_even_with_png_headers(self):
        self.assertFalse(_is_png_response(self._response(), b"not-a-png"))

    def test_rejects_wrong_or_missing_processor_contract_headers(self):
        self.assertFalse(
            _is_png_response(
                self._response(content_type="application/octet-stream"),
                self.PNG,
            )
        )
        self.assertFalse(
            _is_png_response(
                self._response(output_format=""),
                self.PNG,
            )
        )
