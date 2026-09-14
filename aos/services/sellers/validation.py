"""Central Seller request validation and mass-assignment policy."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import time
from typing import Any, Iterable

from .constants import (
    ABOUT_BUSINESS_MAX_LENGTH,
    BUSINESS_CATEGORY_MAX_LENGTH,
    DEFAULT_LIST_LIMIT,
    LOCATION_FILTER_MAX_LENGTH,
    MAX_CURSOR_LENGTH,
    MAX_LIST_LIMIT,
    MAX_OPERATING_HOURS_ROWS,
    OPERATING_DAYS,
    SEARCH_MAX_LENGTH,
    SELLER_TYPES,
    VALID_FOLLOW_FILTERS,
    VALID_SORTS,
)
from .errors import SellerValidationError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_SCRIPT_SCHEME_RE = re.compile(r"(?:javascript|data|vbscript)\s*:", re.IGNORECASE)
_TIME_RE = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)(?::(?P<second>[0-5]\d))?$")
_DISALLOWED_INVISIBLE = frozenset(
    {
        "\u200b",
        "\u2060",
        "\ufeff",
        *[chr(value) for value in range(0x202A, 0x202F)],
        *[chr(value) for value in range(0x2066, 0x206A)],
    }
)


def ensure_known_fields(payload: dict[str, Any], allowed: Iterable[str]) -> None:
    allowed_set = set(allowed)
    unknown = sorted(str(key) for key in payload if key not in allowed_set)
    if unknown:
        raise SellerValidationError(
            f"Unsupported seller fields: {', '.join(unknown)}.",
            code="INVALID_SELLER_REQUEST",
        )


def normalize_identifier(
    value: Any,
    *,
    field: str,
    required: bool = True,
    max_length: int = 140,
) -> str:
    if isinstance(value, (dict, list, tuple, set, bool)):
        raise SellerValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        if required:
            raise SellerValidationError(
                f"{field.replace('_', ' ').title()} is required.",
                code=f"INVALID_{field.upper()}",
            )
        return ""
    if len(text) > max_length or not _IDENTIFIER_RE.fullmatch(text):
        raise SellerValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    return text


def normalize_optional_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    multiline: bool,
) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list, tuple, set, bool)):
        raise SellerValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    text = unicodedata.normalize("NFC", str(value))
    if "\x00" in text:
        raise SellerValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    for char in text:
        if unicodedata.category(char) == "Cc" and char not in {"\n", "\r", "\t"}:
            raise SellerValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
        if char in _DISALLOWED_INVISIBLE:
            raise SellerValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text) if multiline else re.sub(r"\s+", " ", text)
    if len(text) > max_length:
        raise SellerValidationError(
            f"{field.replace('_', ' ').title()} cannot exceed {max_length} characters.",
            code=f"INVALID_{field.upper()}",
        )
    if _HTML_TAG_RE.search(text) or _SCRIPT_SCHEME_RE.search(text):
        raise SellerValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    return text


def normalize_business_category(value: Any) -> str:
    return normalize_optional_text(
        value,
        field="business_category",
        max_length=BUSINESS_CATEGORY_MAX_LENGTH,
        multiline=False,
    )


def normalize_about_business(value: Any) -> str:
    return normalize_optional_text(
        value,
        field="about_business",
        max_length=ABOUT_BUSINESS_MAX_LENGTH,
        multiline=True,
    )


def normalize_search(value: Any) -> str:
    return normalize_optional_text(
        value,
        field="search_query",
        max_length=SEARCH_MAX_LENGTH,
        multiline=False,
    )


def normalize_location_filter(value: Any, *, field: str) -> str:
    return normalize_optional_text(
        value,
        field=field,
        max_length=LOCATION_FILTER_MAX_LENGTH,
        multiline=False,
    )


def normalize_seller_type(value: Any, *, required: bool = False) -> str:
    text = normalize_optional_text(
        value,
        field="seller_type",
        max_length=32,
        multiline=False,
    )
    if not text and not required:
        return ""
    if text not in SELLER_TYPES:
        raise SellerValidationError("Invalid seller type.", code="INVALID_SELLER_TYPE")
    return text


def normalize_sort(value: Any) -> str:
    text = normalize_optional_text(value, field="seller_sort", max_length=32, multiline=False)
    text = text or "recommended"
    if text not in VALID_SORTS:
        raise SellerValidationError("Invalid seller sort.", code="INVALID_SELLER_SORT")
    return text


def normalize_follow_filter(value: Any, *, authenticated: bool) -> str:
    text = normalize_optional_text(value, field="follow_filter", max_length=32, multiline=False)
    if not text:
        return ""
    if text not in VALID_FOLLOW_FILTERS:
        raise SellerValidationError("Invalid follow filter.", code="INVALID_SELLER_FILTER")
    if not authenticated:
        raise SellerValidationError(
            "Login is required to filter sellers by follow status.",
            code="AUTH_REQUIRED",
            http_status=401,
        )
    return text


def normalize_country_code(value: Any) -> str:
    text = normalize_optional_text(value, field="country_code", max_length=2, multiline=False).upper()
    if text and (len(text) != 2 or not text.isalpha()):
        raise SellerValidationError("Invalid country code.", code="INVALID_COUNTRY")
    return text


def normalize_optional_boolean(value: Any, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in {0, 1}:
        return value
    if isinstance(value, (dict, list, tuple, set, float)):
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_FILTER")
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return 1
    if text in {"0", "false", "no"}:
        return 0
    raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_FILTER")


def _strict_integer(value: Any, *, default: int, field: str) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, float)):
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_PAGINATION")
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+", text):
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_PAGINATION")
    try:
        return int(text)
    except (TypeError, ValueError, OverflowError):
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_PAGINATION")


def normalize_pagination(payload: dict[str, Any]) -> tuple[int, str]:
    limit = _strict_integer(payload.get("limit"), default=DEFAULT_LIST_LIMIT, field="limit")
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise SellerValidationError("Invalid seller pagination.", code="INVALID_SELLER_PAGINATION")
    cursor_value = payload.get("cursor")
    if cursor_value in (None, ""):
        return limit, ""
    if isinstance(cursor_value, (dict, list, tuple, set, bool, bytes, bytearray)):
        raise SellerValidationError("Invalid seller cursor.", code="INVALID_SELLER_PAGINATION")
    cursor = str(cursor_value).strip()
    if not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise SellerValidationError("Invalid seller cursor.", code="INVALID_SELLER_PAGINATION")
    return limit, cursor


def normalize_expected_version(value: Any) -> int | None:
    if value in (None, ""):
        return None
    result = _strict_integer(value, default=0, field="expected_version")
    if result < 0:
        raise SellerValidationError("Invalid seller version.", code="INVALID_SELLER_VERSION")
    return result


def normalize_clear_banner(value: Any) -> bool:
    parsed = normalize_optional_boolean(value, field="clear_shop_banner")
    return bool(parsed) if parsed is not None else False


def _parse_time(value: Any, *, field: str) -> str:
    if isinstance(value, time):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, (dict, list, tuple, set, bool)):
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_OPERATING_HOURS")
    text = str(value or "").strip()
    match = _TIME_RE.fullmatch(text)
    if not match:
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_OPERATING_HOURS")
    return f"{match.group('hour')}:{match.group('minute')}:{match.group('second') or '00'}"


def _time_seconds(value: str) -> int:
    hour, minute, second = (int(part) for part in value.split(":"))
    return hour * 3600 + minute * 60 + second


def normalize_operating_hours(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        if len(value) > 12_000:
            raise SellerValidationError("Invalid operating hours.", code="INVALID_OPERATING_HOURS")
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise SellerValidationError("Invalid operating hours.", code="INVALID_OPERATING_HOURS")
    if not isinstance(value, list) or len(value) > MAX_OPERATING_HOURS_ROWS:
        raise SellerValidationError("Invalid operating hours.", code="INVALID_OPERATING_HOURS")

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed = {"day_of_week", "is_open", "open_time", "close_time"}
    for raw in value:
        if not isinstance(raw, dict):
            raise SellerValidationError("Invalid operating hours.", code="INVALID_OPERATING_HOURS")
        unknown = sorted(str(key) for key in raw if key not in allowed)
        if unknown:
            raise SellerValidationError("Invalid operating hours.", code="INVALID_OPERATING_HOURS")
        day = normalize_optional_text(
            raw.get("day_of_week"),
            field="day_of_week",
            max_length=16,
            multiline=False,
        )
        if day not in OPERATING_DAYS or day in seen:
            raise SellerValidationError("Invalid operating hours.", code="INVALID_OPERATING_HOURS")
        seen.add(day)
        is_open_value = normalize_optional_boolean(raw.get("is_open"), field="is_open")
        is_open = bool(is_open_value) if is_open_value is not None else False
        open_time = None
        close_time = None
        if is_open:
            open_time = _parse_time(raw.get("open_time"), field="open_time")
            close_time = _parse_time(raw.get("close_time"), field="close_time")
            if _time_seconds(open_time) >= _time_seconds(close_time):
                raise SellerValidationError("Invalid operating hours.", code="INVALID_OPERATING_HOURS")
        result.append(
            {
                "day_of_week": day,
                "is_open": 1 if is_open else 0,
                "open_time": open_time,
                "close_time": close_time,
            }
        )
    order = {day: index for index, day in enumerate(OPERATING_DAYS)}
    result.sort(key=lambda row: order[row["day_of_week"]])
    return result


def normalize_coordinate(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_LOCATION")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_LOCATION")
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise SellerValidationError(f"Invalid {field}.", code="INVALID_SELLER_LOCATION")
    return round(result, 7)


def normalize_radius(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        raise SellerValidationError("Invalid radius.", code="INVALID_SELLER_LOCATION")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise SellerValidationError("Invalid radius.", code="INVALID_SELLER_LOCATION")
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise SellerValidationError("Invalid radius.", code="INVALID_SELLER_LOCATION")
    return round(result, 2)
