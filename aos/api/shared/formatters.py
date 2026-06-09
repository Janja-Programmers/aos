"""
Shared API response formatting helpers.
"""

from __future__ import annotations

from typing import Any


def to_non_negative_int(value: Any) -> int:
    """Safely normalize a value into a non-negative integer."""

    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        normalized = 0

    return max(normalized, 0)


def to_float(value: Any) -> float:
    """Safely normalize a value into a float."""

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def humanize_count(value: Any) -> str:
    """
    Convert a count into a compact presentation-ready value.

    Examples:
        0                 -> "0"
        999               -> "999"
        1_000             -> "1K"
        1_250             -> "1.3K"
        15_900            -> "15.9K"
        999_999           -> "1M"
        1_200_000         -> "1.2M"
        1_000_000_000     -> "1B"
        1_000_000_000_000 -> "1T"
    """

    count = to_non_negative_int(value)

    units = (
        (1_000_000_000_000, "T"),
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    )

    for index, (threshold, suffix) in enumerate(units):
        if count < threshold:
            continue

        compact_value = count / threshold

        if compact_value >= 999.5 and index > 0:
            next_threshold, next_suffix = units[index - 1]
            next_value = count / next_threshold
            formatted = f"{next_value:.1f}".rstrip("0").rstrip(".")

            return f"{formatted}{next_suffix}"

        if compact_value >= 100:
            formatted = f"{compact_value:.0f}"
        else:
            formatted = f"{compact_value:.1f}"

        formatted = formatted.rstrip("0").rstrip(".")

        return f"{formatted}{suffix}"

    return str(count)


def format_rating(value: Any) -> str:
    """
    Format a rating for display.

    Examples:
        0   -> "0"
        1   -> "1"
        4.5 -> "4.5"
    """

    rating = to_float(value)
    formatted = f"{rating:.1f}"

    return formatted.rstrip("0").rstrip(".")
