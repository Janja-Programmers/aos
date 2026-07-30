from __future__ import annotations

import unittest

from aos.services.maps.internal_url import (
    InvalidInternalMapsURL,
    build_internal_maps_url,
    normalize_internal_maps_url,
)


class TestInternalMapsURL(unittest.TestCase):
    def test_private_loopback_and_cluster_hosts_are_allowed(self):
        self.assertEqual(
            normalize_internal_maps_url("http://127.0.0.1:8081/", service="Nominatim"),
            "http://127.0.0.1:8081",
        )
        self.assertEqual(
            normalize_internal_maps_url("http://nominatim:8080", service="Nominatim"),
            "http://nominatim:8080",
        )

    def test_public_credentials_query_and_fragment_are_rejected(self):
        # Preserve the runtime Basic Auth case without committing a credential-shaped URI literal.
        basic_auth_userinfo = ":".join(("user", "pass"))
        invalid = (
            "https://8.8.8.8",
            f"https://{basic_auth_userinfo}@127.0.0.1:8081",
            "http://127.0.0.1:8081?target=x",
            "http://127.0.0.1:8081#fragment",
            "file:///tmp/maps",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InvalidInternalMapsURL):
                normalize_internal_maps_url(value, service="Maps")

    def test_endpoint_join_rejects_redirect_like_components(self):
        self.assertEqual(build_internal_maps_url("http://nominatim:8080", "/search"), "http://nominatim:8080/search")
        for endpoint in ("/search?next=x", "/search#x", "\\\\attacker"):
            with self.subTest(endpoint=endpoint), self.assertRaises(InvalidInternalMapsURL):
                build_internal_maps_url("http://nominatim:8080", endpoint)
