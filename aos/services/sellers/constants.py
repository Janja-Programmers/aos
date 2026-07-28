"""Canonical Seller domain constants."""

from __future__ import annotations

SELLER_DOCTYPE = "AOS Seller"
PROFILE_DOCTYPE = "AOS Profile"
USER_DOCTYPE = "User"

STATUS_ACTIVE = "Active"
STATUS_SUSPENDED = "Suspended"
STATUS_DELETED = "Deleted"
SELLER_STATUSES = frozenset({STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_DELETED})
PUBLIC_SELLER_STATUSES = frozenset({STATUS_ACTIVE})

SELLER_TYPE_INDIVIDUAL = "Individual"
SELLER_TYPE_BUSINESS = "Business"
SELLER_TYPES = frozenset({SELLER_TYPE_INDIVIDUAL, SELLER_TYPE_BUSINESS})

BUSINESS_CATEGORY_MAX_LENGTH = 140
ABOUT_BUSINESS_MAX_LENGTH = 2000
SEARCH_MAX_LENGTH = 100
LOCATION_FILTER_MAX_LENGTH = 140
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
MAX_LIST_OFFSET = 10_000

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
        "q",
        "seller_type",
        "business_category",
        "category",
        "follow_filter",
        "locality",
        "region",
        "country_code",
        "is_verified",
        "has_location",
        "sort",
        "limit",
        "offset",
        # Existing seller-list near-me compatibility. Maps remains the owner
        # of supported-area and coordinate policy.
        "latitude",
        "lat",
        "longitude",
        "lon",
        "lng",
        "radius_km",
    }
)
GET_ALLOWED_FIELDS = frozenset({"seller", "seller_id", "id"})
STATUS_ALLOWED_FIELDS = frozenset()
UPDATE_ALLOWED_FIELDS = frozenset(
    {
        "business_category",
        "about_business",
        "operating_hours",
        "shop_banner",
        "shop_banner_media",
        "banner_media",
        "media_id",
        "clear_shop_banner",
        "expected_version",
    }
)

RATE_LIMITS = {
    "list_public": 300,
    "get_public": 240,
    "get_status": 120,
    "update": 20,
}
