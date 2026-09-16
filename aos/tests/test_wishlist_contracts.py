from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe

from aos.api.v1 import wishlist as wishlist_v1
from aos.services.wishlist.service import WishlistService
from aos.services.wishlist.validation import (
    decode_wishlist_cursor,
    encode_wishlist_cursor,
    normalize_wishlist_list_request,
    normalize_wishlist_mutation,
)

ROOT = Path(__file__).resolve().parents[1]


def test_wishlist_cursor_round_trips_and_list_contract_is_narrow():
    request = normalize_wishlist_list_request({})
    token = encode_wishlist_cursor(
        saved_on="2026-07-27 12:00:00.000001",
        name="WISH-0123456789abcdef0123456789abcdef",
    )

    assert request["limit"] > 0
    assert set(request) == {"country", "currency", "limit", "cursor"}
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


def test_wishlist_list_rejects_marketplace_filters_and_offset_compatibility():
    for payload in (
        {"offset": 0},
        {"sort": "recent"},
        {"seller": "seller_public"},
        {"category": "category_public"},
        {"q": "phone"},
    ):
        try:
            normalize_wishlist_list_request(payload)
        except Exception as exc:
            assert getattr(exc, "code", "") == "INVALID_WISHLIST_REQUEST"
        else:
            raise AssertionError(f"non-canonical Wishlist list payload was accepted: {payload}")


def test_wishlist_mutation_rejects_legacy_aliases_and_toggle_state():
    for payload in (
        {"id": "ad_public"},
        {"ad_id": "ad_public", "wishlisted": 1},
        {"listing_id": "ad_public"},
    ):
        try:
            normalize_wishlist_mutation(payload)
        except Exception as exc:
            assert getattr(exc, "code", "") == "INVALID_WISHLIST_REQUEST"
        else:
            raise AssertionError(f"legacy Wishlist payload was accepted: {payload}")


def test_wishlist_wrappers_expose_only_explicit_add_remove_and_list_methods():
    path = ROOT / "api" / "v1" / "wishlist" / "__init__.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert "toggle_wishlist" not in functions
    assert {"add_to_wishlist", "remove_from_wishlist", "list_wishlist"}.issubset(functions)

    for name in ("add_to_wishlist", "remove_from_wishlist"):
        decorator = functions[name].decorator_list[0]
        assert isinstance(decorator, ast.Call)
        methods = next(keyword.value for keyword in decorator.keywords if keyword.arg == "methods")
        assert isinstance(methods, ast.List)
        assert [item.value for item in methods.elts if isinstance(item, ast.Constant)] == ["POST"]

    list_decorator = functions["list_wishlist"].decorator_list[0]
    list_methods = next(keyword.value for keyword in list_decorator.keywords if keyword.arg == "methods")
    assert isinstance(list_methods, ast.List)
    assert [item.value for item in list_methods.elts if isinstance(item, ast.Constant)] == ["GET"]


def test_v1_wishlist_transport_strips_cmd_before_strict_validation():
    response = {"ok": True}
    with patch.object(wishlist_v1, "_add_to_wishlist_impl", return_value=response) as add_impl:
        result = wishlist_v1.add_to_wishlist(
            cmd="aos.api.v1.wishlist.add_to_wishlist",
            ad_id="ad_public",
            unexpected="must-remain-visible-to-validator",
        )
    assert result is response
    add_impl.assert_called_once_with(
        ad_id="ad_public",
        unexpected="must-remain-visible-to-validator",
    )

    with patch.object(wishlist_v1, "_remove_from_wishlist_impl", return_value=response) as remove_impl:
        result = wishlist_v1.remove_from_wishlist(
            cmd="aos.api.v1.wishlist.remove_from_wishlist",
            ad_id="ad_public",
            unexpected="must-remain-visible-to-validator",
        )
    assert result is response
    remove_impl.assert_called_once_with(
        ad_id="ad_public",
        unexpected="must-remain-visible-to-validator",
    )

    with patch.object(wishlist_v1, "_list_wishlist_impl", return_value=response) as list_impl:
        result = wishlist_v1.list_wishlist(
            cmd="aos.api.v1.wishlist.list_wishlist",
            limit="20",
            unexpected="must-remain-visible-to-validator",
        )
    assert result is response
    list_impl.assert_called_once_with(
        limit="20",
        unexpected="must-remain-visible-to-validator",
    )


def test_wishlist_api_uses_explicit_domain_methods_and_canonical_ads_projection():
    mutation = (ROOT / "api" / "wishlist" / "mutation.py").read_text(encoding="utf-8")
    add_source = (ROOT / "api" / "wishlist" / "add.py").read_text(encoding="utf-8")
    remove_source = (ROOT / "api" / "wishlist" / "remove.py").read_text(encoding="utf-8")
    list_source = (ROOT / "api" / "wishlist" / "list.py").read_text(encoding="utf-8")
    service = (ROOT / "services" / "wishlist" / "service.py").read_text(encoding="utf-8")

    assert "service.add(" in add_source
    assert "service.remove(" in remove_source
    assert "set_state" not in service
    assert "rate_limit_key(" in mutation
    assert "rate_limit_key(" in list_source
    assert "load_public_ad_items" in list_source
    assert "project_ad_image_urls" not in list_source
    assert "AOS Seller" not in list_source
    assert "AOS Exchange Rate" not in list_source
    assert "require_public_ad_for_viewer" in service
    assert "resolve_ad_name" in service
    assert "frappe.db.commit()" not in mutation
    assert '"aos.tasks.wishlist.sync_wishlist_activity"' in mutation
    assert "enqueue_after_commit=True" in mutation

    activity_task = (ROOT / "tasks" / "wishlist.py").read_text(encoding="utf-8")
    assert '"AOS Wishlist"' in activity_task
    assert '"status": "Active"' in activity_task


def test_active_wishlist_card_state_is_always_bounded_by_candidate_ads():
    shared = (ROOT / "api" / "shared" / "utils.py").read_text(encoding="utf-8")
    ads_list = (ROOT / "api" / "ads" / "list_ads.py").read_text(encoding="utf-8")
    projection = (ROOT / "services" / "marketplace_discovery" / "projection.py").read_text(encoding="utf-8")

    assert "ad_ids: Iterable[str]" in shared
    assert '"ad": ["in", bounded]' in shared
    assert "get_active_wishlist_ad_ids(viewer, ad_ids=names)" in ads_list
    assert "get_active_wishlist_ad_ids(viewer, ad_ids=names)" in projection


def test_concurrent_first_add_recovers_from_database_duplicate_and_converges():
    service = WishlistService()
    candidate = MagicMock()
    candidate.insert.side_effect = frappe.DuplicateEntryError("duplicate")
    winner = MagicMock()
    winner.status = "Active"

    with (
        patch.object(service, "_locked_relationship", side_effect=[None, winner]),
        patch("aos.services.wishlist.service.frappe.get_doc", return_value=candidate),
        patch("aos.services.wishlist.service.frappe.db.savepoint"),
        patch("aos.services.wishlist.service.frappe.db.rollback") as rollback,
    ):
        changed = service._activate(user="buyer@example.com", ad_name="AD-0001")

    assert changed is False
    candidate.insert.assert_called_once_with(ignore_permissions=True)
    rollback.assert_called_once()
    winner.save.assert_not_called()


def test_existing_relationship_lookup_uses_database_row_lock():
    service = WishlistService()
    winner = MagicMock()
    with (
        patch.object(service, "_relationship_name", return_value="WISH-ROW"),
        patch("aos.services.wishlist.service.frappe.get_doc", return_value=winner) as get_doc,
    ):
        locked = service._locked_relationship(user="buyer@example.com", ad_name="AD-0001")

    assert locked is winner
    get_doc.assert_called_once_with("AOS Wishlist", "WISH-ROW", for_update=True)
