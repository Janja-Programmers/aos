from __future__ import annotations

import unittest

from aos.services.verification.errors import VerificationValidationError
from aos.services.verification.validation import normalize_submit_payload


class TestVerificationValidation(unittest.TestCase):
    def _individual(self, **overrides):
        payload = {
            "verification_type": "Individual",
            "legal_name": "Jane Doe",
            "phone_number": "+254700000001",
            "idempotency_key": "verification-test-key",
            "verification_documents": [
                {"document_type": "Identity Document", "media_id": "MEDIA-ABC123"}
            ],
        }
        payload.update(overrides)
        return payload

    def _business(self, **overrides):
        payload = {
            "verification_type": "Business",
            "business_name": "AOS Shop",
            "business_type": "Limited Company",
            "business_category": "Retail",
            "business_phone_number": "+254700000002",
            "business_email": "SHOP@example.com",
            "business_website": "https://example.com",
            "business_address": "Nairobi",
            "idempotency_key": "business-verification-key",
            "verification_documents": [
                {"document_type": "Registration Document", "media_id": "MEDIA-ABC124"}
            ],
        }
        payload.update(overrides)
        return payload

    def test_transport_metadata_is_not_a_domain_alias(self):
        with self.assertRaises(VerificationValidationError) as ctx:
            normalize_submit_payload(
                self._individual(cmd="aos.api.v1.verification.submit_verification")
            )
        self.assertEqual(ctx.exception.code, "VERIFICATION_UNKNOWN_FIELD")

    def test_unknown_business_field_is_rejected(self):
        with self.assertRaises(VerificationValidationError) as ctx:
            normalize_submit_payload(self._individual(status="Approved"))
        self.assertEqual(ctx.exception.code, "VERIFICATION_UNKNOWN_FIELD")

    def test_legacy_document_media_alias_is_rejected(self):
        with self.assertRaises(VerificationValidationError) as ctx:
            normalize_submit_payload(
                self._individual(
                    verification_documents=[
                        {"document_type": "ID", "media": "MEDIA-ABC123"}
                    ]
                )
            )
        self.assertEqual(ctx.exception.code, "VERIFICATION_UNKNOWN_FIELD")

    def test_unknown_document_field_is_rejected(self):
        with self.assertRaises(VerificationValidationError) as ctx:
            normalize_submit_payload(
                self._individual(
                    verification_documents=[
                        {
                            "document_type": "ID",
                            "media_id": "MEDIA-ABC123",
                            "url": "https://evil.test/x",
                        }
                    ]
                )
            )
        self.assertEqual(ctx.exception.code, "VERIFICATION_UNKNOWN_FIELD")

    def test_duplicate_media_is_rejected(self):
        with self.assertRaises(VerificationValidationError) as ctx:
            normalize_submit_payload(
                self._individual(
                    verification_documents=[
                        {"document_type": "Front", "media_id": "MEDIA-ABC123"},
                        {"document_type": "Back", "media_id": "MEDIA-ABC123"},
                    ]
                )
            )
        self.assertEqual(ctx.exception.code, "VERIFICATION_DUPLICATE_DOCUMENT")

    def test_missing_idempotency_key_is_rejected(self):
        with self.assertRaises(VerificationValidationError) as ctx:
            normalize_submit_payload(self._individual(idempotency_key=""))
        self.assertEqual(ctx.exception.code, "VERIFICATION_INVALID_IDEMPOTENCY_KEY")

    def test_short_idempotency_key_is_rejected(self):
        with self.assertRaises(VerificationValidationError) as ctx:
            normalize_submit_payload(self._individual(idempotency_key="short"))
        self.assertEqual(ctx.exception.code, "VERIFICATION_INVALID_IDEMPOTENCY_KEY")

    def test_invalid_date_order_is_rejected(self):
        with self.assertRaises(VerificationValidationError):
            normalize_submit_payload(
                self._individual(
                    verification_documents=[
                        {
                            "document_type": "ID",
                            "media_id": "MEDIA-ABC123",
                            "issue_date": "2026-08-10",
                            "expiry_date": "2026-08-09",
                        }
                    ]
                )
            )

    def test_business_fields_are_normalized_without_seller_dependency(self):
        result = normalize_submit_payload(self._business())
        self.assertEqual(result["business_email"], "shop@example.com")
        self.assertEqual(result["business_type"], "Limited Company")
        self.assertEqual(result["business_category"], "Retail")
        self.assertEqual(result["business_website"], "https://example.com")

    def test_invalid_business_type_is_rejected(self):
        with self.assertRaises(VerificationValidationError):
            normalize_submit_payload(self._business(business_type="LLC"))

    def test_too_many_documents_is_rejected(self):
        documents = [
            {"document_type": f"Doc {index}", "media_id": f"MEDIA-ABC{index:03d}"}
            for index in range(11)
        ]
        with self.assertRaises(VerificationValidationError):
            normalize_submit_payload(self._individual(verification_documents=documents))


if __name__ == "__main__":
    unittest.main()
