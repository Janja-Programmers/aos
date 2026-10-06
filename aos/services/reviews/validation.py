"""Central Reviews input validation and mass-assignment policy."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from aos.services.media.identifiers import normalize_media_id

from .constants import (
    COMMENT_MAX_LENGTH,
    COMMENT_MIN_LENGTH,
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    MAX_REVIEW_IMAGES,
    RATING_MAX,
    RATING_MIN,
    RATING_STEP,
    REACTIONS,
    TITLE_MAX_LENGTH,
    TITLE_MIN_LENGTH,
)
from .errors import ReviewValidationError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_SCRIPT_SCHEME_RE = re.compile(r"(?:javascript|data|vbscript)\s*:", re.IGNORECASE)
_REPEAT_RE = re.compile(r"(.)\1{39,}", re.DOTALL)
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
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
        raise ReviewValidationError(
            f"Unsupported review fields: {', '.join(unknown)}.",
            code="INVALID_REVIEW_REQUEST",
        )


def normalize_identifier(
    value: Any,
    *,
    field: str,
    required: bool = True,
    max_length: int = 140,
) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        if required:
            raise ReviewValidationError(
                f"{field.replace('_', ' ').title()} is required.",
                code=f"INVALID_{field.upper()}",
            )
        return ""
    if len(text) > max_length or not _IDENTIFIER_RE.fullmatch(text):
        raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    return text


def normalize_version(value: Any, *, required: bool = True) -> str:
    if isinstance(value, (dict, list, tuple, set, bool)):
        raise ReviewValidationError("Invalid review version.", code="INVALID_REVIEW_VERSION")
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        if required:
            raise ReviewValidationError("Review version is required.", code="INVALID_REVIEW_VERSION")
        return ""
    if len(text) > 32 or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?",
        text,
    ):
        raise ReviewValidationError("Invalid review version.", code="INVALID_REVIEW_VERSION")
    try:
        datetime.fromisoformat(text)
    except ValueError:
        raise ReviewValidationError("Invalid review version.", code="INVALID_REVIEW_VERSION") from None
    return text


def normalize_rating(value: Any) -> int:
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        raise ReviewValidationError("Invalid rating.", code="INVALID_RATING")
    try:
        rating = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ReviewValidationError("Invalid rating.", code="INVALID_RATING") from None
    if not rating.is_finite():
        raise ReviewValidationError("Invalid rating.", code="INVALID_RATING")
    if rating < RATING_MIN or rating > RATING_MAX:
        raise ReviewValidationError("Rating must be between 1 and 5.", code="INVALID_RATING")
    step = Decimal(str(RATING_STEP))
    if rating % step != 0:
        raise ReviewValidationError("Rating must use whole stars.", code="INVALID_RATING")
    result = int(rating)
    if not math.isfinite(float(result)):
        raise ReviewValidationError("Invalid rating.", code="INVALID_RATING")
    return result


def _normalize_text(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
    required: bool,
    multiline: bool,
) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    text = unicodedata.normalize("NFC", str(value or ""))
    if "\x00" in text:
        raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    for char in text:
        category = unicodedata.category(char)
        if category == "Cc" and char not in {"\n", "\t"}:
            raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
        if char in _DISALLOWED_INVISIBLE:
            raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text) if multiline else re.sub(r"\s+", " ", text)
    if required and len(text) < minimum:
        raise ReviewValidationError(
            f"{field.replace('_', ' ').title()} is required.",
            code=f"INVALID_{field.upper()}",
        )
    if len(text) > maximum:
        raise ReviewValidationError(
            f"{field.replace('_', ' ').title()} is too long.",
            code=f"INVALID_{field.upper()}",
        )
    if _HTML_TAG_RE.search(text) or _SCRIPT_SCHEME_RE.search(text):
        raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    if _REPEAT_RE.search(text):
        raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    if len(_URL_RE.findall(text)) > 3:
        raise ReviewValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    return text


def normalize_title(value: Any, *, required: bool = True) -> str:
    return _normalize_text(
        value,
        field="review_title",
        minimum=TITLE_MIN_LENGTH,
        maximum=TITLE_MAX_LENGTH,
        required=required,
        multiline=False,
    )


def normalize_comment(value: Any, *, required: bool = True) -> str:
    return _normalize_text(
        value,
        field="review_text",
        minimum=COMMENT_MIN_LENGTH,
        maximum=COMMENT_MAX_LENGTH,
        required=required,
        multiline=True,
    )


def normalize_images(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ReviewValidationError("Media must be a list.", code="INVALID_REVIEW_MEDIA")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ReviewValidationError("Invalid review media.", code="INVALID_REVIEW_MEDIA")
        media_id = normalize_media_id(item)
        if media_id is None:
            raise ReviewValidationError("Invalid review media.", code="INVALID_REVIEW_MEDIA")
        if media_id in seen:
            raise ReviewValidationError("Duplicate review media.", code="INVALID_REVIEW_MEDIA")
        seen.add(media_id)
        normalized.append(media_id)
    if len(normalized) > MAX_REVIEW_IMAGES:
        raise ReviewValidationError("Too many review images.", code="INVALID_REVIEW_MEDIA")
    return normalized


def _strict_integer(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, float)):
        raise ReviewValidationError("Invalid pagination.", code="INVALID_REVIEW_PAGINATION")
    text = str(value).strip()
    if not re.fullmatch(r"\d+", text):
        raise ReviewValidationError("Invalid pagination.", code="INVALID_REVIEW_PAGINATION")
    try:
        return int(text)
    except (TypeError, ValueError, OverflowError):
        raise ReviewValidationError("Invalid pagination.", code="INVALID_REVIEW_PAGINATION") from None


def normalize_limit(value: Any) -> int:
    limit = _strict_integer(value, default=DEFAULT_LIST_LIMIT)
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise ReviewValidationError("Invalid pagination.", code="INVALID_REVIEW_PAGINATION")
    return limit


def normalize_flag(value: Any, *, field: str) -> bool | None:
    if value in (None, ""):
        return None
    if value in (True, 1, "1", "true", "True"):
        return True
    if value in (False, 0, "0", "false", "False"):
        return False
    raise ReviewValidationError(f"Invalid {field}.", code="INVALID_REVIEW_FILTER")


def normalize_rating_filter(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return normalize_rating(value)


def normalize_reaction(value: Any) -> str:
    reaction = str(value or "").strip().title()
    if reaction not in REACTIONS:
        raise ReviewValidationError("Invalid reaction.", code="INVALID_REVIEW_REACTION")
    return reaction


def normalize_moderation_reason(value: Any) -> str:
    return _normalize_text(
        value,
        field="moderation_reason",
        minimum=0,
        maximum=1000,
        required=False,
        multiline=True,
    )
