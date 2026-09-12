from __future__ import annotations

import unittest

from aos.api.maps.serializers import serialize_photon_place, serialize_place, serialize_reverse_geocode_result


class TestMapsSerialization(unittest.TestCase):
    def test_photon_result_is_provider_neutral_and_geojson_ordered(self):
        result = serialize_photon_place({
            "geometry": {"type": "Point", "coordinates": [139.6917, 35.6895]},
            "properties": {
                "osm_type": "N",
                "osm_id": 123,
                "name": "Tokyo",
                "country": "Japan",
                "countrycode": "JP",
                "city": "Tokyo",
                "osm_key": "place",
                "osm_value": "city",
                "extent": [139.5, 35.8, 139.9, 35.5],
            },
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["place_id"], "osm:node:123")
        self.assertEqual(result["latitude"], 35.6895)
        self.assertEqual(result["longitude"], 139.6917)
        self.assertEqual(result["country_code"], "JP")
        self.assertNotIn("source", result)

    def test_nominatim_transient_place_id_is_not_exposed_as_stable_identity(self):
        result = serialize_place({
            "place_id": 999999,
            "osm_type": "way",
            "osm_id": 456,
            "lat": "51.5074",
            "lon": "-0.1278",
            "display_name": "London, United Kingdom",
            "address": {"city": "London", "country": "United Kingdom", "country_code": "gb"},
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["place_id"], "osm:way:456")
        self.assertNotEqual(result["place_id"], 999999)

    def test_reverse_result_uses_same_stable_identity(self):
        result = serialize_reverse_geocode_result({
            "place_id": 77,
            "osm_type": "relation",
            "osm_id": 88,
            "lat": "-33.8688",
            "lon": "151.2093",
            "display_name": "Sydney, Australia",
            "address": {"city": "Sydney", "country": "Australia", "country_code": "au"},
        })
        self.assertEqual(result["place_id"], "osm:relation:88")

    def test_malformed_provider_geometry_is_ignored(self):
        self.assertIsNone(serialize_photon_place({"geometry": {"coordinates": [1]}, "properties": {}}))
        self.assertIsNone(serialize_photon_place({"geometry": {"coordinates": ["x", "y"]}, "properties": {}}))
        self.assertIsNone(serialize_place({"lat": "x", "lon": 1}))
