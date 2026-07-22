from __future__ import annotations

import datetime as dt
import unittest
import unicodedata

from aos.services.accounts.errors import AccountValidationError
from aos.services.accounts.validation import (
    validate_bio,
    validate_date_of_birth,
    validate_display_name,
    validate_legal_name,
    validate_phone,
    validate_profile_patch,
)


class AccountValidationTests(unittest.TestCase):
    def test_display_name_normalizes_unicode_and_whitespace(self):
        value = validate_display_name("  Jose\u0301   Kalutu ")
        self.assertEqual(value, unicodedata.normalize("NFC", "José Kalutu"))

    def test_display_name_rejects_empty(self):
        with self.assertRaises(AccountValidationError):
            validate_display_name("   ")

    def test_display_name_rejects_null_byte(self):
        with self.assertRaises(AccountValidationError):
            validate_display_name("Dan\x00Kalutu")

    def test_legal_name_can_be_empty(self):
        self.assertEqual(validate_legal_name(""), "")

    def test_legal_name_rejects_too_short(self):
        with self.assertRaises(AccountValidationError):
            validate_legal_name("A")

    def test_bio_enforces_length(self):
        with self.assertRaises(AccountValidationError):
            validate_bio("x" * 501)

    def test_phone_normalizes_international_format(self):
        self.assertEqual(validate_phone("00 254 712-345-678"), "+254712345678")

    def test_phone_rejects_local_ambiguous_number(self):
        with self.assertRaises(AccountValidationError):
            validate_phone("0712345678")

    def test_date_rejects_future(self):
        with self.assertRaises(AccountValidationError):
            validate_date_of_birth(dt.date.today() + dt.timedelta(days=1))

    def test_date_accepts_iso(self):
        self.assertEqual(validate_date_of_birth("2000-01-02"), dt.date(2000, 1, 2))

    def test_patch_rejects_unknown_field(self):
        with self.assertRaises(AccountValidationError):
            validate_profile_patch({"roles": ["System Manager"]})

    def test_patch_rejects_empty_update(self):
        with self.assertRaises(AccountValidationError):
            validate_profile_patch({})

    def test_patch_maps_legacy_aliases(self):
        patch = validate_profile_patch({"full_name": "Dan Kalutu", "mobile_no": "+254712345678"})
        self.assertEqual(patch["display_name"], "Dan Kalutu")
        self.assertEqual(patch["phone"], "+254712345678")


if __name__ == "__main__":
    unittest.main()
