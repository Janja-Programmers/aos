from __future__ import annotations

import unittest

from aos.services.maps.errors import MapsValidationError
from aos.services.maps.validation import (
    validate_autocomplete_request,
    validate_map_points_request,
    validate_remove_seller_location_request,
    validate_route_request,
    validate_search_request,
    validate_set_seller_location_request,
)


class TestMapsValidation(unittest.TestCase):
    def test_search_normalizes_aliases_and_bounds(self):
        request = validate_search_request({"q": "  Nairobi   CBD ", "limit": "5", "bounded": "true"})
        self.assertEqual(request["query"], "Nairobi CBD")
        self.assertEqual(request["limit"], 5)
        self.assertTrue(request["bounded"])

    def test_unknown_structured_and_conflicting_inputs_are_rejected(self):
        invalid = (
            lambda: validate_search_request({"query": {"bad": True}}),
            lambda: validate_search_request({"query": "Nairobi", "q": "Mombasa"}),
            lambda: validate_autocomplete_request({"query": "Nairobi", "attacker": "x"}),
            lambda: validate_route_request({"locations": [{"latitude": -1.2, "longitude": 36.8, "x": 1}]}),
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(MapsValidationError):
                operation()

    def test_route_is_bounded_and_coverage_checked(self):
        route = validate_route_request(
            {
                "origin_latitude": -1.286389,
                "origin_longitude": 36.817223,
                "destination_latitude": -1.292066,
                "destination_longitude": 36.821946,
            }
        )
        self.assertEqual(len(route["locations"]), 2)
        with self.assertRaises(MapsValidationError):
            validate_route_request(
                {
                    "origin_latitude": 51.5,
                    "origin_longitude": -0.1,
                    "destination_latitude": -1.2,
                    "destination_longitude": 36.8,
                }
            )

    def test_map_viewport_is_clipped_and_zoom_is_strict(self):
        request = validate_map_points_request(
            {"north": 5.8, "south": -5.3, "east": 42.3, "west": 33.4, "zoom": 8}
        )
        self.assertLessEqual(request["north"], 5.7)
        self.assertGreaterEqual(request["south"], -5.2)
        with self.assertRaises(MapsValidationError):
            validate_map_points_request(
                {"north": 5, "south": -5, "east": 42, "west": 33.5, "zoom": 15}
            )

    def test_location_mutations_use_strict_versions_and_public_text(self):
        request = validate_set_seller_location_request(
            {
                "latitude": -1.286389,
                "longitude": 36.817223,
                "location_name": "  Main Shop ",
                "expected_version": "0",
            }
        )
        self.assertEqual(request["location_name"], "Main Shop")
        self.assertEqual(request["expected_version"], 0)
        self.assertEqual(validate_remove_seller_location_request({"expected_version": 2})["expected_version"], 2)
        for invalid in (True, -1, 1.5, "x"):
            with self.subTest(invalid=invalid), self.assertRaises(MapsValidationError):
                validate_remove_seller_location_request({"expected_version": invalid})
