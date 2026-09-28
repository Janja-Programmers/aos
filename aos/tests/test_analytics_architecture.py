from __future__ import annotations

from pathlib import Path

from frappe.tests.utils import FrappeTestCase


ROOT = Path(__file__).resolve().parents[1]


class TestAnalyticsArchitecture(FrappeTestCase):
    def test_generic_public_ingestion_is_not_exposed(self):
        source = (ROOT / "api" / "v1" / "analytics_pipeline" / "__init__.py").read_text()
        self.assertNotIn("def track_event(", source)
        self.assertNotIn("def track_events(", source)
        self.assertIn("def handle_callback(", source)

    def test_shared_pipeline_uses_opaque_actor_identity(self):
        service = (ROOT / "services" / "analytics_pipeline_service.py").read_text()
        schema = (ROOT / "aos" / "doctype" / "aos_analytics_ingest_job" / "aos_analytics_ingest_job.json").read_text()
        self.assertIn('"actor_account_id"', service)
        self.assertIn('"actor_account_id"', schema)
        self.assertNotIn('"fieldname": "user"', schema)
        self.assertNotIn('"options": "User"', schema)

    def test_shared_taxonomy_is_allowlisted(self):
        taxonomy = (ROOT / "services" / "analytics_taxonomy.py").read_text()
        self.assertIn('"ad_detail_view"', taxonomy)
        self.assertIn("Unknown server analytics event", taxonomy)
        self.assertNotIn('"ad_view"', taxonomy)
