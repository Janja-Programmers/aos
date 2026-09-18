from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
AD_JS = ROOT / "aos" / "doctype" / "aos_ad" / "aos_ad.js"
AD_JSON = ROOT / "aos" / "doctype" / "aos_ad" / "aos_ad.json"


class TestAdsDeskReviewContract(unittest.TestCase):
    def test_status_remains_server_owned(self):
        data = json.loads(AD_JSON.read_text(encoding="utf-8"))
        status = next(field for field in data["fields"] if field.get("fieldname") == "status")
        self.assertEqual(status.get("read_only"), 1)

    def test_review_endpoint_does_not_require_marketplace_preference(self):
        review_api = (ROOT / "api" / "ads" / "review.py").read_text(encoding="utf-8")
        self.assertIn("require_authenticated_user", review_api)
        self.assertNotIn("require_login", review_api)
        self.assertIn("reviewer=user", review_api)

    def test_desk_review_uses_canonical_review_endpoint(self):
        source = AD_JS.read_text(encoding="utf-8")
        self.assertIn('"aos.api.v1.ads.review_ad"', source)
        self.assertIn("ad_id: publicId", source)
        self.assertIn("decision,", source)
        self.assertIn("reason,", source)
        self.assertIn("version,", source)
        self.assertIn("await frm.reload_doc()", source)

    def test_buttons_follow_server_reviewable_states(self):
        source = AD_JS.read_text(encoding="utf-8")
        self.assertIn('new Set(["Reviewing", "Declined"])', source)
        self.assertIn('new Set(["Reviewing", "Active"])', source)
        self.assertIn('fieldname: "reason"', source)
        self.assertIn("reqd: 1", source)

    def test_desk_does_not_mutate_status_directly(self):
        source = AD_JS.read_text(encoding="utf-8")
        self.assertNotIn('set_value("status"', source)
        self.assertNotIn("set_value('status'", source)
        self.assertNotIn("frappe.db.set_value", source)


if __name__ == "__main__":
    unittest.main()
