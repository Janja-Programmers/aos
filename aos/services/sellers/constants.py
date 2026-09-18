"""Canonical Seller domain constants."""

from __future__ import annotations

SELLER_DOCTYPE = "AOS Seller"
PROFILE_DOCTYPE = "AOS Profile"
USER_DOCTYPE = "User"

STATUS_ACTIVE = "Active"
STATUS_SUSPENDED = "Suspended"
STATUS_CLOSED = "Closed"
SELLER_STATUSES = frozenset({STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_CLOSED})
PUBLIC_SELLER_STATUSES = frozenset({STATUS_ACTIVE})

SELLER_TYPE_INDIVIDUAL = "Individual"
SELLER_TYPE_BUSINESS = "Business"
SELLER_TYPES = frozenset({SELLER_TYPE_INDIVIDUAL, SELLER_TYPE_BUSINESS})

BUSINESS_CATEGORY_MAX_LENGTH = 140
ABOUT_BUSINESS_MAX_LENGTH = 2000
SEARCH_MAX_LENGTH = 100
LOCATION_FILTER_MAX_LENGTH = 140
LOCATION_NAME_MAX_LENGTH = 140
LOCATION_INSTRUCTIONS_MAX_LENGTH = 500
DISPLAY_ADDRESS_MAX_LENGTH = 500
LOCALITY_MAX_LENGTH = 140
REGION_MAX_LENGTH = 140
STATUS_REASON_CODE_MAX_LENGTH = 64
STATUS_SOURCE_MAX_LENGTH = 64

OPERATING_DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
MAX_OPERATING_HOURS_ROWS = 7

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50
MAX_CURSOR_LENGTH = 2048

VALID_FOLLOW_FILTERS = frozenset({"following", "not_following"})
VALID_SORTS = frozenset(
    {
        "recommended",
        "nearest",
        "rating",
        "newest",
        "most_ads",
        "most_reviewed",
    }
)

LIST_ALLOWED_FIELDS = frozenset(
    {
        "search",
        "seller_type",
        "business_category",
        "follow_filter",
        "locality",
        "region",
        "country_code",
        "is_verified",
        "has_location",
        "sort",
        "limit",
        "cursor",
        "latitude",
        "longitude",
        "radius_km",
    }
)
GET_ALLOWED_FIELDS = frozenset({"seller_id"})
STATUS_ALLOWED_FIELDS = frozenset()
UPDATE_ALLOWED_FIELDS = frozenset(
    {
        "business_category",
        "about_business",
        "operating_hours",
        "shop_banner_media_id",
        "clear_shop_banner",
        "expected_version",
    }
)

# Shared application limiter limits. Public reads are also subject to the
# deployment-wide Nginx baseline in ci/public-endpoint-rate-limits.json.
RATE_LIMITS = {
    "list_public_ip": 300,
    "list_public_user": 180,
    "get_public_ip": 240,
    "get_public_user": 180,
    "get_status_user": 120,
    "update_user": 20,
    "update_ip": 60,
    "set_location_user": 10,
    "set_location_ip": 30,
    "remove_location_user": 5,
    "remove_location_ip": 20,
    "get_location_ip": 240,
    "get_location_user": 120,
    "map_points_ip": 180,
}

NEARBY_SELLERS_DEFAULT_RADIUS_KM = 10.0
NEARBY_SELLERS_MAX_RADIUS_KM = 100.0
NEARBY_SELLERS_MIN_RADIUS_KM = 0.1

SELLER_MAP_POINTS_DEFAULT_ZOOM = 12
SELLER_MAP_POINTS_MIN_ZOOM = 3
SELLER_MAP_POINTS_MAX_ZOOM = 20
SELLER_MAP_POINTS_MAX_RAW_SELLERS = 5000
SELLER_MAP_POINTS_MAX_ITEMS = 300
