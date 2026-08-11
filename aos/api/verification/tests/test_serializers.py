from __future__ import annotations

import unittest

from aos.services.verification.serializers import mask_document_number


class TestVerificationSerializers(unittest.TestCase):
    def test_document_number_is_masked(self):
        self.assertEqual(mask_document_number("123456789"), "*****6789")
        self.assertEqual(mask_document_number("1234"), "****")
        self.assertIsNone(mask_document_number(""))


if __name__ == "__main__":
    unittest.main()
