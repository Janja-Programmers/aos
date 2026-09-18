from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REQUEST_JSON = ROOT / "aos" / "doctype" / "aos_verification_request" / "aos_verification_request.json"
REQUEST_JS = ROOT / "aos" / "doctype" / "aos_verification_request" / "aos_verification_request.js"
REQUEST_PY = ROOT / "aos" / "doctype" / "aos_verification_request" / "aos_verification_request.py"
DESK_REVIEW_PY = ROOT / "aos" / "doctype" / "aos_verification_request" / "review.py"
REVIEW_SERVICE = ROOT / "services" / "verification" / "review.py"
LIFECYCLE = ROOT / "services" / "verification" / "lifecycle.py"
V1_API = ROOT / "api" / "v1" / "verification" / "__init__.py"


class TestVerificationDeskReviewContract(unittest.TestCase):
    def test_workflow_fields_are_server_owned(self):
        data = json.loads(REQUEST_JSON.read_text(encoding="utf-8"))
        fields = {row["fieldname"]: row for row in data["fields"]}
        self.assertEqual(fields["status"].get("read_only"), 1)
        self.assertEqual(fields["rejection_reason"].get("read_only"), 1)
        self.assertNotIn("mandatory_depends_on", fields["rejection_reason"])

    def test_desk_exposes_explicit_review_actions_only(self):
        source = REQUEST_JS.read_text(encoding="utf-8")
        self.assertIn('status === "Pending"', source)
        self.assertIn('status === "Reviewing"', source)
        self.assertIn('status === "Approved"', source)
        for action in ("start_review", "approve", "reject", "revoke"):
            self.assertIn(f'"{action}"', source)
        self.assertIn('fieldname: "reason"', source)
        self.assertIn("reqd: 1", source)
        self.assertIn("version,", source)
        self.assertIn("await frm.reload_doc()", source)
        self.assertNotIn('set_value("status"', source)
        self.assertNotIn("set_value('status'", source)
        self.assertNotIn("frappe.db.set_value", source)

    def test_desk_method_is_staff_only_and_uses_domain_review_service(self):
        controller = REQUEST_PY.read_text(encoding="utf-8")
        desk_review = DESK_REVIEW_PY.read_text(encoding="utf-8")
        service = REVIEW_SERVICE.read_text(encoding="utf-8")
        self.assertNotIn('@frappe.whitelist', controller)
        self.assertIn('@frappe.whitelist(methods=["POST"])', desk_review)
        self.assertIn("apply_review_action", desk_review)
        self.assertIn("reviewer=reviewer", desk_review)
        self.assertIn("assert_reviewer(reviewer)", service)
        self.assertIn("REVIEW_ACTION_TRANSITIONS", service)
        self.assertIn("aos_verification_expected_modified", service)
        self.assertIn("doc.save(ignore_permissions=True)", service)

    def test_direct_status_assignment_is_not_a_reviewer_contract(self):
        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        self.assertIn("REVIEW_ACTION_TRANSITIONS.get(action)", lifecycle)
        self.assertIn("must use an explicit review action", lifecycle)
        self.assertIn("review action is not allowed in the current state", lifecycle)

    def test_public_verification_api_remains_owner_only(self):
        source = V1_API.read_text(encoding="utf-8")
        self.assertIn("def submit_verification", source)
        self.assertIn("def get_my_verification", source)
        self.assertNotIn("review_verification", source)
        self.assertNotIn("approve_verification", source)
        self.assertNotIn("revoke_verification", source)


if __name__ == "__main__":
    unittest.main()
