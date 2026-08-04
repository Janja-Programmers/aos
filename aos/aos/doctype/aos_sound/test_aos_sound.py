# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

from __future__ import annotations

from types import SimpleNamespace

from frappe.tests import IntegrationTestCase

from aos.aos.doctype.aos_sound.aos_sound import AOSSound


EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestAOSSound(IntegrationTestCase):
    def test_commercial_source_is_always_commercial_safe(self):
        sound = SimpleNamespace(source_type="commercial", is_commercial_safe=0)
        AOSSound._normalize_flags(sound)
        self.assertEqual(sound.is_commercial_safe, 1)
