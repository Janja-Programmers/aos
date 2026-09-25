from __future__ import annotations

import json
from pathlib import Path
import unittest

from aos.services.moderation_contract import CONTENT_KIND_TARGET_DOCTYPES, validate_moderation_target


class TestModerationContract(unittest.TestCase):
    def test_only_real_hardened_consumers_are_canonical(self):
        self.assertEqual(
            CONTENT_KIND_TARGET_DOCTYPES,
            {"ad": "AOS Ad", "review": "AOS Review", "short": "AOS Short"},
        )
        for kind, doctype in CONTENT_KIND_TARGET_DOCTYPES.items():
            self.assertEqual(validate_moderation_target(content_kind=kind, target_doctype=doctype), (kind, doctype))

        for kind, doctype in (
            ("profile", "AOS Profile"),
            ("seller", "AOS Seller"),
            ("live", "AOS Live Stream"),
            ("message", "AOS Message"),
            ("media", "AOS Media Object"),
        ):
            with self.assertRaises(ValueError):
                validate_moderation_target(content_kind=kind, target_doctype=doctype)

    def test_desk_select_does_not_advertise_phantom_integrations(self):
        path = Path(__file__).resolve().parents[1] / "aos" / "doctype" / "aos_moderation_job" / "aos_moderation_job.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        field = next(item for item in payload["fields"] if item.get("fieldname") == "content_kind")
        self.assertEqual(field.get("options"), "ad\nreview\nshort")
