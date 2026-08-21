# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from aos.aos.doctype.aos_sound.aos_sound import AOSSound


EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestAOSSound(IntegrationTestCase):
    def test_commercial_source_is_always_commercial_safe(self):
        sound = SimpleNamespace(source_type="commercial", is_commercial_safe=0)
        AOSSound._normalize_flags(sound)
        self.assertEqual(sound.is_commercial_safe, 1)

    def test_original_sound_owner_is_authoritative_source_short_creator(self):
        sound = SimpleNamespace(
            source_type="original",
            created_from_short="SHORT-2026-00001",
            owner="Guest",
        )

        with patch(
            "aos.aos.doctype.aos_sound.aos_sound.frappe.db.get_value",
            return_value="creator@example.test",
        ) as get_value:
            AOSSound._bind_original_sound_owner(sound)

        self.assertEqual(sound.owner, "creator@example.test")
        get_value.assert_called_once_with("AOS Short", "SHORT-2026-00001", "owner")

    def test_original_sound_owner_cannot_be_forged_by_caller(self):
        sound = SimpleNamespace(
            source_type="original",
            created_from_short="SHORT-2026-00001",
            owner="other-user@example.test",
        )

        with patch(
            "aos.aos.doctype.aos_sound.aos_sound.frappe.db.get_value",
            return_value="creator@example.test",
        ):
            AOSSound._bind_original_sound_owner(sound)

        self.assertEqual(sound.owner, "creator@example.test")

    def test_catalog_sound_owner_is_not_rebound(self):
        sound = SimpleNamespace(
            source_type="uploaded",
            created_from_short=None,
            owner="Administrator",
        )

        with patch(
            "aos.aos.doctype.aos_sound.aos_sound.frappe.db.get_value"
        ) as get_value:
            AOSSound._bind_original_sound_owner(sound)

        self.assertEqual(sound.owner, "Administrator")
        get_value.assert_not_called()
