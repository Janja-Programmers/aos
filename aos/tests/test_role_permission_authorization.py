"""Source guards for role-permission-backed administrative authorization."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestRolePermissionAuthorization(TestCase):
    def test_permission_helper_uses_frappe_permission_engine(self):
        source = _source("utils/doctype_permissions.py")
        self.assertIn("frappe.has_permission", source)
        self.assertIn("user=clean_user", source)
        self.assertNotIn("System Manager", source)

    def test_media_management_has_no_system_manager_role_bypass(self):
        service = _source("services/media/media_service.py")
        authorization = _source("services/media/resource_authorization.py")
        purposes = _source("services/media/media_purposes.py")
        self.assertNotIn('"System Manager"', service)
        self.assertNotIn('"System Manager"', authorization)
        self.assertNotIn("allowed_roles", purposes)
        self.assertIn('required_permission_doctype="AOS Category"', purposes)

    def test_doctype_controllers_use_permissions_not_privileged_role_names(self):
        for relative in (
            "aos/doctype/aos_ad/aos_ad.py",
            "aos/doctype/aos_seller/aos_seller.py",
            "aos/doctype/aos_review/aos_review.py",
            "aos/doctype/aos_review_reaction/aos_review_reaction.py",
            "aos/doctype/aos_wishlist/aos_wishlist.py",
        ):
            with self.subTest(relative=relative):
                source = _source(relative)
                self.assertNotIn('"System Manager"', source)
                self.assertIn("has_doctype_permission", source)

    def test_desk_uploaders_use_effective_form_permissions(self):
        category = _source("aos/doctype/aos_category/aos_category.js")
        sound = _source("aos/doctype/aos_sound/aos_sound.js")
        self.assertIn("frm.perm && frm.perm[0]", category)
        self.assertIn("levelZero.write", category)
        self.assertIn("frm.perm && frm.perm[0]", sound)
        self.assertNotIn('user_roles.includes("System Manager")', category)
        self.assertNotIn('user_roles.includes("System Manager")', sound)

    def test_application_code_has_no_hardcoded_system_manager_authorization(self):
        for suffix in ("*.py", "*.js"):
            for path in ROOT.rglob(suffix):
                if "tests" in path.parts or "__pycache__" in path.parts:
                    continue
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertNotIn("System Manager", path.read_text(encoding="utf-8"))

