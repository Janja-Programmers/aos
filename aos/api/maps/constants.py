"""
Maps API constants.

Used by:
- place search
- autocomplete
- reverse geocoding
- routing
- internal Photon, Nominatim and Valhalla clients
"""

from __future__ import annotations


# RATE LIMITS

# Public search-as-you-type endpoint. Flutter should still debounce requests.
AUTOCOMPLETE_PLACES_LIMIT_PER_MINUTE_PER_IP = 240

# Public place-search endpoint.
SEARCH_PLACES_LIMIT_PER_MINUTE_PER_IP = 120

# Public reverse-geocoding endpoint.
REVERSE_GEOCODE_LIMIT_PER_MINUTE_PER_IP = 180

# Route requests are more expensive than geocoding and require login.
GET_ROUTE_LIMIT_PER_MINUTE_PER_USER = 60
REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_USER = 30

# Best-effort extra IP shield for authenticated route requests.
GET_ROUTE_LIMIT_PER_MINUTE_PER_IP = 300
REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_IP = 180


# INTERNAL SERVICE URLS

# These defaults work when Frappe runs directly on the same host as Docker
# services bound to localhost.
#
# They can be overridden through site_config.json or common_site_config.json.
DEFAULT_NOMINATIM_BASE_URL = "http://127.0.0.1:8081"
DEFAULT_PHOTON_BASE_URL = "http://127.0.0.1:2322"
DEFAULT_VALHALLA_BASE_URL = "http://127.0.0.1:8002"


# TIMEOUTS

# Connection timeout for opening a connection to an internal map service.
MAP_SERVICE_CONNECT_TIMEOUT_SECONDS = 5

# Photon should be fast because it powers autocomplete.
PHOTON_REQUEST_TIMEOUT_SECONDS = 10

# Nominatim queries can occasionally take longer while caches are cold.
NOMINATIM_REQUEST_TIMEOUT_SECONDS = 30

# Valhalla route generation should usually be fast for country-level data.
VALHALLA_REQUEST_TIMEOUT_SECONDS = 20


# SEARCH / AUTOCOMPLETE

AUTOCOMPLETE_DEFAULT_LIMIT = 5
AUTOCOMPLETE_MAX_LIMIT = 8

SEARCH_DEFAULT_LIMIT = 5
SEARCH_MAX_LIMIT = 10

SEARCH_MIN_QUERY_LENGTH = 2
SEARCH_MAX_QUERY_LENGTH = 200

# Restrict Version 1 search to Kenya.
DEFAULT_SEARCH_COUNTRY_CODES = "ke"

# Nominatim viewbox order:
# west, north, east, south
#
# Kenya-wide coverage with a small buffer for border/coastal edge cases.
KENYA_VIEWBOX = "33.50,5.70,42.20,-5.20"

DEFAULT_SEARCH_BOUNDED = True

SEARCH_ADDRESS_DETAILS = True

# Search providers.
GEOCODER_PRIMARY_PHOTON = "photon"
GEOCODER_PRIMARY_NOMINATIM = "nominatim"
GEOCODER_PRIMARY_ALLOWED = {
    GEOCODER_PRIMARY_PHOTON,
    GEOCODER_PRIMARY_NOMINATIM,
}

DEFAULT_GEOCODER_PRIMARY = GEOCODER_PRIMARY_NOMINATIM
DEFAULT_GEOCODER_FALLBACK = GEOCODER_PRIMARY_PHOTON


# REVERSE GEOCODING

REVERSE_GEOCODE_DEFAULT_ZOOM = 18
REVERSE_GEOCODE_ADDRESS_DETAILS = True


# COORDINATES

LATITUDE_MIN = -90.0
LATITUDE_MAX = 90.0

LONGITUDE_MIN = -180.0
LONGITUDE_MAX = 180.0

COORDINATE_PRECISION = 7


# SUPPORTED MAP COVERAGE

# Kenya map extract bounding box.
#
# west, south, east, north
KENYA_BBOX_WEST = 33.50
KENYA_BBOX_SOUTH = -5.20
KENYA_BBOX_EAST = 42.20
KENYA_BBOX_NORTH = 5.70

SUPPORTED_COUNTRY_CODE = "KE"
SUPPORTED_COUNTRY_CODE_LOWER = "ke"


# ROUTING

ROUTE_DEFAULT_COSTING = "auto"

ROUTE_ALLOWED_COSTINGS = {
    "auto",
    "bicycle",
    "pedestrian",
}

ROUTE_DEFAULT_UNITS = "kilometers"

ROUTE_ALLOWED_UNITS = {
    "kilometers",
    "miles",
}

ROUTE_DEFAULT_LANGUAGE = "en-US"

ROUTE_MAX_LOCATIONS = 10

ROUTE_SHAPE_FORMAT = "polyline6"


# CACHE

# Very short cache for repeated autocomplete requests.
AUTOCOMPLETE_CACHE_TTL_SECONDS = 120

# Short cache for repeated search requests.
SEARCH_CACHE_TTL_SECONDS = 300

# Reverse-geocoded coordinates rarely change.
REVERSE_GEOCODE_CACHE_TTL_SECONDS = 86400

# Routes can be reused briefly, but should not remain cached for too long.
ROUTE_CACHE_TTL_SECONDS = 120


# RESPONSE / PAYLOAD LIMITS

PLACE_NAME_MAX_LENGTH = 300
DISPLAY_ADDRESS_MAX_LENGTH = 1000
LOCALITY_MAX_LENGTH = 200
REGION_MAX_LENGTH = 200
POSTCODE_MAX_LENGTH = 40
COUNTRY_NAME_MAX_LENGTH = 120
COUNTRY_CODE_LENGTH = 2

ROUTE_LANGUAGE_MAX_LENGTH = 20


# CONFIGURATION KEYS

# Site config keys:
#
# {
#   "nominatim_base_url": "http://127.0.0.1:8081",
#   "photon_base_url": "http://127.0.0.1:2322",
#   "valhalla_base_url": "http://127.0.0.1:8002",
#   "maps_geocoder_primary": "nominatim",
#   "maps_geocoder_fallback": "photon",
#   "maps_photon_enabled": false
# }
NOMINATIM_BASE_URL_CONFIG_KEY = "nominatim_base_url"
PHOTON_BASE_URL_CONFIG_KEY = "photon_base_url"
VALHALLA_BASE_URL_CONFIG_KEY = "valhalla_base_url"

MAPS_GEOCODER_PRIMARY_CONFIG_KEY = "maps_geocoder_primary"
MAPS_GEOCODER_FALLBACK_CONFIG_KEY = "maps_geocoder_fallback"
