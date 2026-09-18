from __future__ import annotations

import unittest

from aos.services.maps.errors import MapsValidationError
from aos.services.maps.validation import (
    validate_autocomplete_request,
    validate_map_points_request,
    validate_remove_seller_location_request,
    validate_reverse_geocode_request,
    validate_route_request,
    validate_search_request,
    validate_set_seller_location_request,
)


class TestMapsValidation(unittest.TestCase):
    def test_search_is_global_canonical_and_bounded(self):
        request = validate_search_request({"q": "  Tokyo   Station ", "limit": "999", "country_code": "jp"})
        self.assertEqual(request["query"], "Tokyo Station")
        self.assertEqual(request["limit"], 20)
        self.assertEqual(request["country_code"], "JP")
        for invalid in ({"query": "Tokyo"}, {"q": "Tokyo", "bounded": True}, {"q": "Tokyo", "lat": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(MapsValidationError):
                validate_search_request(invalid)

    def test_autocomplete_bias_requires_canonical_coordinate_pair(self):
        request = validate_autocomplete_request(
            {"q": "Shinjuku", "latitude": 35.6895, "longitude": 139.6917, "language": "ja"}
        )
        self.assertEqual(request["latitude"], 35.6895)
        self.assertEqual(request["longitude"], 139.6917)
        with self.assertRaises(MapsValidationError):
            validate_autocomplete_request({"q": "Shinjuku", "latitude": 35.6})

    def test_global_wgs84_coordinate_ranges(self):
        for latitude, longitude in ((51.5074, -0.1278), (-33.8688, 151.2093), (64.1466, -21.9426), (0, 179.9999)):
            request = validate_reverse_geocode_request({"latitude": latitude, "longitude": longitude})
            self.assertEqual(request["latitude"], latitude)
            self.assertEqual(request["longitude"], longitude)
        for bad in ((90.1, 0), (-90.1, 0), (0, 180.1), (0, -180.1)):
            with self.subTest(bad=bad), self.assertRaises(MapsValidationError):
                validate_reverse_geocode_request({"latitude": bad[0], "longitude": bad[1]})

    def test_route_uses_one_canonical_location_shape(self):
        route = validate_route_request(
            {"locations": [
                {"latitude": 51.5074, "longitude": -0.1278},
                {"latitude": 48.8566, "longitude": 2.3522},
            ], "costing": "auto"}
        )
        self.assertEqual(len(route["locations"]), 2)
        for invalid in (
            {"origin_latitude": 1, "origin_longitude": 2, "destination_latitude": 3, "destination_longitude": 4},
            {"locations": [{"latitude": 1, "longitude": 2, "lng": 2}, {"latitude": 3, "longitude": 4}]},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(MapsValidationError):
                validate_route_request(invalid)

    def test_global_viewport_supports_antimeridian_and_rejects_unbounded_scans(self):
        request = validate_map_points_request(
            {"north": 12, "south": -12, "west": 170, "east": -170, "zoom": 7}
        )
        self.assertTrue(request["crosses_antimeridian"])
        with self.assertRaises(MapsValidationError):
            validate_map_points_request({"north": 80, "south": -80, "west": -170, "east": 170, "zoom": 2})
        with self.assertRaises(MapsValidationError):
            validate_map_points_request({"north": 5, "south": -5, "west": -10, "east": 10, "zoom": 15})

    def test_location_mutations_use_strict_versions_and_global_coordinates(self):
        request = validate_set_seller_location_request(
            {"latitude": -33.8688, "longitude": 151.2093, "location_name": "  Main Shop ", "expected_version": "0"}
        )
        self.assertEqual(request["location_name"], "Main Shop")
        self.assertEqual(request["expected_version"], 0)
        self.assertEqual(validate_remove_seller_location_request({"expected_version": 2})["expected_version"], 2)
        for invalid in (True, -1, 1.5, "x"):
            with self.subTest(invalid=invalid), self.assertRaises(MapsValidationError):
                validate_remove_seller_location_request({"expected_version": invalid})
