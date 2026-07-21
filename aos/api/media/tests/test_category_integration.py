from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from aos.aos.doctype.aos_category.aos_category import AOSCategory


class TestCategoryMediaHooks(FrappeTestCase):
    def test_empty_icon_relationship_is_install_safe_noop(self):
        category = SimpleNamespace(icon_media="", _previous_icon_media_id="")

        with patch("aos.aos.doctype.aos_category.aos_category.MediaService") as service_factory:
            AOSCategory._finalize_icon_media_relationship(category)

        service_factory.assert_not_called()
