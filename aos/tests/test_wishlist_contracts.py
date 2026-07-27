from __future__ import annotations

import ast
from pathlib import Path

from aos.api.wishlist.list import _build_order_by
from aos.services.ads.validation import (
    decode_wishlist_cursor,
    encode_wishlist_cursor,
    normalize_wishlist_list_filters,
)


ROOT = Path(__file__).resolve().parents[1]


def test_wishlist_default_sort_is_recent_and_cursor_round_trips():
    filters = normalize_wishlist_list_filters({})
    token = encode_wishlist_cursor(
        saved_on="2026-07-27 12:00:00.000001",
        name="WISH-0123456789abcdef0123456789abcdef",
    )

    assert filters["sort"] == "recent"
    assert decode_wishlist_cursor(token) == (
        "2026-07-27 12:00:00.000001",
        "WISH-0123456789abcdef0123456789abcdef",
    )


def test_wishlist_cursor_rejects_invalid_datetime_payload():
    token = encode_wishlist_cursor(
        saved_on="not-a-datetime",
        name="WISH-0123456789abcdef0123456789abcdef",
    )

    try:
        decode_wishlist_cursor(token)
    except Exception as exc:
        assert getattr(exc, "code", "") == "INVALID_WISHLIST_CURSOR"
    else:
        raise AssertionError("invalid cursor datetime was accepted")


def test_explicit_wishlist_sorts_remain_primary():
    kwargs = {
        "cursor_mode": False,
        "saved_on_sql": "COALESCE(w.saved_on, w.creation)",
        "geo_boost": "geo_rank",
        "verified_boost": "verified_rank",
        "conversion_available_sql": "conversion_available",
        "current_price_sql": "current_price",
    }

    recent = _build_order_by(sort="recent", **kwargs)
    rating = _build_order_by(sort="rating_high", **kwargs)
    price_low = _build_order_by(sort="price_low", **kwargs)
    price_high = _build_order_by(sort="price_high", **kwargs)

    assert recent.startswith("COALESCE(w.saved_on, w.creation) DESC")
    assert rating.startswith("a.average_rating DESC")
    assert price_low.startswith("conversion_available DESC, current_price ASC")
    assert price_high.startswith("conversion_available DESC, current_price DESC")


def test_wishlist_wrappers_keep_read_and_write_http_methods_separate():
    path = ROOT / "api" / "v1" / "wishlist" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    toggle = functions["toggle_wishlist"]
    listing = functions["list_wishlist"]
    toggle_decorator = toggle.decorator_list[0]
    list_decorator = listing.decorator_list[0]

    assert isinstance(toggle_decorator, ast.Call)
    methods = next(keyword.value for keyword in toggle_decorator.keywords if keyword.arg == "methods")
    assert isinstance(methods, ast.List)
    assert [item.value for item in methods.elts if isinstance(item, ast.Constant)] == ["POST"]
    assert isinstance(list_decorator, ast.Call)
    list_methods = next(keyword.value for keyword in list_decorator.keywords if keyword.arg == "methods")
    assert isinstance(list_methods, ast.List)
    assert [item.value for item in list_methods.elts if isinstance(item, ast.Constant)] == ["GET"]


def test_wishlist_api_uses_domain_service_and_safe_rate_limit_keys():
    toggle_source = (ROOT / "api" / "wishlist" / "toggle.py").read_text(encoding="utf-8")
    list_source = (ROOT / "api" / "wishlist" / "list.py").read_text(encoding="utf-8")

    assert "WishlistService().set_state" in toggle_source
    assert "rate_limit_key(" in toggle_source
    assert "rate_limit_key(" in list_source
    assert "FOR UPDATE" not in toggle_source
