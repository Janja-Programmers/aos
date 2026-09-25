"""Batch current-domain projection for Activity Center reads.

The Activity table stores historical presentation snapshots.  This module
re-validates the referenced resource against the owning hardened domain before
those snapshots are returned.  Database rows remain history; unavailable
resources are projected as a safe marker instead of leaking stale metadata.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import today

from aos.services.accounts.serializers import serialize_internal_identity_map
from aos.services.activity.observability import activity_log
from aos.services.ads.authorization import get_seller_for_user
from aos.services.marketplace_discovery.projection import public_ad_sql_context
from aos.services.shorts.policy import filter_viewable_rows
from aos.services.social.capabilities import SocialCapabilityService


def _value(row: dict[str, Any], key: str, default=None):
    return row.get(key, default)


def _unavailable(row: dict[str, Any]) -> None:
    row["resource_available"] = False
    row["target_title"] = "Unavailable"
    row["target_subtitle"] = None
    row["target_image"] = None
    row["route_type"] = None
    row["route_id"] = None
    row["metadata_json"] = {}


def _project_ads(rows: list[dict[str, Any]], *, viewer: str) -> None:
    ad_rows = [row for row in rows if row.get("route_type") == "ad"]
    if not ad_rows:
        return
    public_ids = sorted({str(row.get("route_id") or "") for row in ad_rows if row.get("route_id")})
    if not public_ids:
        for row in ad_rows:
            _unavailable(row)
        return

    public_types = {"ad_view", "ad_wishlist", "ad_report"}
    public_ids_to_check = sorted({str(row["route_id"]) for row in ad_rows if row.get("activity_type") in public_types})
    visible: dict[str, dict[str, Any]] = {}
    if public_ids_to_check:
        context = public_ad_sql_context(viewer_param="viewer", include_fx=False)
        result = frappe.db.sql(
            f"""
            SELECT a.public_id, a.title
            FROM `tabAOS Ad` a
            {' '.join(context.joins)}
            WHERE a.public_id IN %(ids)s
              AND {' AND '.join(context.conditions)}
            """,
            {"ids": tuple(public_ids_to_check), "viewer": viewer, "today": today()},
            as_dict=True,
        )
        visible.update({str(item.public_id): dict(item) for item in result})

    posted_ids = sorted({str(row["route_id"]) for row in ad_rows if row.get("activity_type") == "ad_posted"})
    if posted_ids:
        seller = get_seller_for_user(viewer, require_active=False)
        if seller and str(getattr(seller, "status", "") or "") == "Active":
            result = frappe.db.sql(
                """
                SELECT public_id, title
                FROM `tabAOS Ad`
                WHERE public_id IN %(ids)s AND seller=%(seller)s AND status != 'Deleted'
                """,
                {"ids": tuple(posted_ids), "seller": seller.name},
                as_dict=True,
            )
            visible.update({str(item.public_id): dict(item) for item in result})

    for row in ad_rows:
        current = visible.get(str(row.get("route_id") or ""))
        if not current:
            _unavailable(row)
            continue
        row["resource_available"] = True
        if current.get("title"):
            row["target_title"] = str(current["title"])[:140]


def _project_profiles(rows: list[dict[str, Any]], *, viewer: str) -> None:
    profile_rows = [row for row in rows if row.get("route_type") == "profile"]
    if not profile_rows:
        return
    account_ids = sorted({str(row.get("route_id") or "") for row in profile_rows if row.get("route_id")})
    mappings = frappe.db.get_all(
        "AOS Profile",
        filters={"name": ["in", account_ids]},
        fields=["name", "user"],
        limit=max(1, len(account_ids)),
    ) if account_ids else []
    user_by_account = {str(item.name): str(item.user) for item in mappings if item.name and item.user}
    users = sorted(set(user_by_account.values()))
    identities = serialize_internal_identity_map(users)
    relations = SocialCapabilityService().projection_map(viewer=viewer, targets=users) if users else {}

    for row in profile_rows:
        account_id = str(row.get("route_id") or "")
        target_user = user_by_account.get(account_id)
        identity = identities.get(target_user or "") or {}
        relation = relations.get(target_user or "") or {}
        account_available = bool(
            target_user
            and identity.get("enabled")
            and not identity.get("is_deleted")
            and str(identity.get("account_status") or "") == "Active"
        )
        if row.get("activity_type") == "user_block":
            # A user's own block-history item may remain usable while only that
            # user is the blocker. If the target has also blocked the viewer,
            # canonical Social privacy wins and the profile fails closed.
            relationship_allows = bool(relation.get("is_blocked_by_me")) and not bool(relation.get("has_blocked_me"))
        else:
            relationship_allows = not bool(relation.get("is_blocked"))
        if not account_available or not relationship_allows:
            _unavailable(row)
            continue
        row["resource_available"] = True
        row["target_title"] = str(identity.get("display_name") or "AOS User")[:140]
        row["target_image"] = identity.get("avatar") or None


def _project_shorts(rows: list[dict[str, Any]], *, viewer: str) -> None:
    short_rows = [row for row in rows if row.get("route_type") == "short"]
    if not short_rows:
        return
    ids = sorted({str(row.get("route_id") or "") for row in short_rows if row.get("route_id")})
    resources = frappe.get_all(
        "AOS Short",
        filters={"name": ["in", ids]},
        fields=["name", "owner", "lifecycle_status", "moderation_status", "processing_status", "audience"],
        limit=max(1, len(ids)),
    ) if ids else []
    visible = {str(item.get("name")) for item in filter_viewable_rows([dict(item) for item in resources], viewer=viewer)}
    for row in short_rows:
        if str(row.get("route_id") or "") not in visible:
            _unavailable(row)
        else:
            row["resource_available"] = True


def _project_live(rows: list[dict[str, Any]], *, viewer: str) -> None:
    live_rows = [row for row in rows if row.get("route_type") == "live"]
    if not live_rows:
        return
    ids = sorted({str(row.get("route_id") or "") for row in live_rows if row.get("route_id")})
    lives = frappe.get_all(
        "AOS Live Stream",
        filters={"name": ["in", ids]},
        fields=["name", "host_user", "title"],
        limit=max(1, len(ids)),
    ) if ids else []
    live_by_id = {str(item.name): dict(item) for item in lives}
    hosts = sorted({str(item.host_user) for item in lives if item.host_user})
    identities = serialize_internal_identity_map(hosts)
    relations = SocialCapabilityService().projection_map(viewer=viewer, targets=hosts) if hosts else {}

    comment_ids = sorted({str(row.get("target_name") or "") for row in live_rows if row.get("activity_type") == "live_comment" and row.get("target_name")})
    messages = frappe.get_all(
        "AOS Live Message",
        filters={"name": ["in", comment_ids]},
        fields=["name", "live_stream", "status", "sender_user", "visible_to_host", "visible_to_viewers"],
        limit=max(1, len(comment_ids)),
    ) if comment_ids else []
    message_by_id = {str(item.name): dict(item) for item in messages}

    for row in live_rows:
        live_id = str(row.get("route_id") or "")
        live = live_by_id.get(live_id) or {}
        host = str(live.get("host_user") or "")
        identity = identities.get(host) or {}
        relation = relations.get(host) or {}
        # Live's canonical get-live read contract permits ended streams; it
        # gates access on account availability + bidirectional Social blocks,
        # not on the active lifecycle state. Activity must not invent a second
        # Live state machine, so historical host/join items follow that rule.
        allowed = bool(
            live
            and identity.get("enabled")
            and not identity.get("is_deleted")
            and str(identity.get("account_status") or "") == "Active"
            and (viewer == host or not relation.get("is_blocked"))
        )
        if allowed and row.get("activity_type") == "live_comment":
            message = message_by_id.get(str(row.get("target_name") or "")) or {}
            visibility = "visible_to_host" if viewer == host else "visible_to_viewers"
            allowed = bool(
                message
                and str(message.get("live_stream") or "") == live_id
                and str(message.get("status") or "") == "active"
                and bool(message.get(visibility))
            )
        if not allowed:
            _unavailable(row)
            continue
        row["resource_available"] = True
        if live.get("title"):
            row["target_title"] = str(live["title"])[:140]


def project_activity_rows(rows: list[Any], *, viewer: str) -> list[dict[str, Any]]:
    """Apply current owning-domain visibility in bounded batches."""
    projected = [dict(row) for row in rows]
    for row in projected:
        row["resource_available"] = True
    projectors = (
        ("ad", _project_ads),
        ("profile", _project_profiles),
        ("short", _project_shorts),
        ("live", _project_live),
    )
    for route_type, projector in projectors:
        try:
            projector(projected, viewer=viewer)
        except Exception:
            # A secondary projection must not turn Activity into a privacy side
            # channel when its owning domain is temporarily unavailable. Fail
            # closed for that resource class while allowing unrelated history.
            for row in projected:
                if row.get("route_type") == route_type:
                    _unavailable(row)
            activity_log("activity.projection", outcome="failure")
    for row in projected:
        route_type = str(row.get("route_type") or "")
        if route_type == "user_search":
            row["resource_available"] = True
        elif route_type not in {"ad", "profile", "short", "live", ""}:
            _unavailable(row)
    return projected
