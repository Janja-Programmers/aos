"""
Maps API constants.

Used by:
- place search
- reverse geocoding
- routing
- internal Nominatim and Valhalla clients
"""

from __future__ import annotations


# RATE LIMITS

# Public place-search endpoint.
SEARCH_PLACES_LIMIT_PER_MINUTE_PER_IP = 120

# Public reverse-geocoding endpoint.
REVERSE_GEOCODE_LIMIT_PER_MINUTE_PER_IP = 180

# Route requests are more expensive than geocoding.
GET_ROUTE_LIMIT_PER_MINUTE_PER_IP = 120


# INTERNAL SERVICE URLS

# These defaults work when Frappe runs directly on the same host as Docker
# services bound to localhost.
#
# They can be overridden through site_config.json or common_site_config.json.
DEFAULT_NOMINATIM_BASE_URL = "http://127.0.0.1:8081"
DEFAULT_VALHALLA_BASE_URL = "http://127.0.0.1:8002"


# TIMEOUTS

# Connection timeout for opening a connection to an internal map service.
MAP_SERVICE_CONNECT_TIMEOUT_SECONDS = 5

# Nominatim queries can occasionally take longer while caches are cold.
NOMINATIM_REQUEST_TIMEOUT_SECONDS = 30

# Valhalla route generation should usually be fast for regional data.
VALHALLA_REQUEST_TIMEOUT_SECONDS = 20


# SEARCH

SEARCH_DEFAULT_LIMIT = 5
SEARCH_MAX_LIMIT = 10

SEARCH_MIN_QUERY_LENGTH = 2
SEARCH_MAX_QUERY_LENGTH = 200

# Restrict Version 1 search to Kenya.
DEFAULT_SEARCH_COUNTRY_CODES = "ke"

# Nominatim viewbox order:
# west, north, east, south
#
# Current supported region:
# Mombasa and its surrounding extracted map area.
MOMBASA_VIEWBOX = "39.45,-3.75,39.95,-4.35"

DEFAULT_SEARCH_BOUNDED = True

SEARCH_ADDRESS_DETAILS = True


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

# Current Mombasa map extract bounding box.
#
# west, south, east, north
MOMBASA_BBOX_WEST = 39.45
MOMBASA_BBOX_SOUTH = -4.35
MOMBASA_BBOX_EAST = 39.95
MOMBASA_BBOX_NORTH = -3.75

SUPPORTED_COUNTRY_CODE = "KE"


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

# Short cache for repeated autocomplete/search requests.
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
#   "valhalla_base_url": "http://127.0.0.1:8002"
# }
NOMINATIM_BASE_URL_CONFIG_KEY = "nominatim_base_url"
VALHALLA_BASE_URL_CONFIG_KEY = "valhalla_base_url"
