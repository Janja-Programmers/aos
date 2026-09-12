from __future__ import annotations

import unittest

from aos.services.sellers.geo import radius_bbox


class TestSellerGeo(unittest.TestCase):
    def test_regular_radius_bbox_is_global_wgs84(self):
        box = radius_bbox(latitude=-1.286389, longitude=36.817223, radius_km=10.0)
        self.assertFalse(box["crosses_antimeridian"])
        self.assertLess(box["south"], -1.286389)
        self.assertGreater(box["north"], -1.286389)
        self.assertLess(box["west"], 36.817223)
        self.assertGreater(box["east"], 36.817223)

    def test_radius_bbox_crosses_antimeridian_in_both_directions(self):
        east = radius_bbox(latitude=0.0, longitude=179.8, radius_km=100.0)
        west = radius_bbox(latitude=0.0, longitude=-179.8, radius_km=100.0)

        self.assertTrue(east["crosses_antimeridian"])
        self.assertGreater(east["west"], 0.0)
        self.assertLess(east["east"], 0.0)
        self.assertTrue(west["crosses_antimeridian"])
        self.assertGreater(west["west"], 0.0)
        self.assertLess(west["east"], 0.0)

    def test_radius_bbox_clips_poles_and_uses_full_longitude_when_needed(self):
        box = radius_bbox(latitude=90.0, longitude=120.0, radius_km=100.0)
        self.assertEqual(box["north"], 90.0)
        self.assertEqual(box["west"], -180.0)
        self.assertEqual(box["east"], 180.0)
        self.assertFalse(box["crosses_antimeridian"])
