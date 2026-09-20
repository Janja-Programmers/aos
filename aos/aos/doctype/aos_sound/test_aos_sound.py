# Copyright (c) 2026, Africa Online Stores and Contributors
from __future__ import annotations

from frappe.tests import IntegrationTestCase
from aos.services.shorts.identity import SOUND_ID_RE, generate_sound_id

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []

class IntegrationTestAOSSound(IntegrationTestCase):
    def test_sound_ids_are_opaque(self):
        self.assertRegex(generate_sound_id(), SOUND_ID_RE)

    def test_sound_ids_do_not_encode_time_or_sequence(self):
        first = generate_sound_id()
        second = generate_sound_id()
        self.assertNotEqual(first, second)
        self.assertFalse(any(token in first for token in ("2026", "00001")))
