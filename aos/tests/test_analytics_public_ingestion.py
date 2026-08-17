from __future__ import annotations

from frappe.tests.utils import FrappeTestCase

from aos.api.analytics_pipeline.events import (
    MAX_EVENT_COLLECTION_ITEMS,
    _prepare_public_event,
)


class TestAnalyticsPublicIngestionBoundary(FrappeTestCase):
    def test_authenticated_identity_and_source_are_server_owned(self):
        event = _prepare_public_event(
            {
                "event_type": "ad_view",
                "user": "victim@example.com",
                "source": "trusted-server",
                "metadata": {"surface": "feed"},
            },
            user="actor@example.com",
            source="api.analytics_pipeline.track_event",
        )

        self.assertEqual(event["user"], "actor@example.com")
        self.assertEqual(event["source"], "api.analytics_pipeline.track_event")

    def test_guest_identity_is_always_anonymous(self):
        event = _prepare_public_event(
            {"event_type": "ad_view", "user": "victim@example.com"},
            user="Guest",
            source="api.analytics_pipeline.track_event",
        )

        self.assertEqual(event["user"], "")

    def test_event_collections_and_nesting_are_bounded(self):
        with self.assertRaises(ValueError):
            _prepare_public_event(
                {
                    "event_type": "ad_view",
                    "metadata": {str(index): index for index in range(MAX_EVENT_COLLECTION_ITEMS + 1)},
                },
                user="Guest",
                source="api.analytics_pipeline.track_event",
            )

        with self.assertRaises(ValueError):
            _prepare_public_event(
                {
                    "event_type": "ad_view",
                    "metadata": {"a": {"b": {"c": {"d": {"e": "too-deep"}}}}},
                },
                user="Guest",
                source="api.analytics_pipeline.track_event",
            )
