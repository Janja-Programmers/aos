"""Stable public AOS Social v1 routes."""

from __future__ import annotations

import frappe

from aos.api.social.block import (
    block_user_impl as _block_user_impl,
    get_block_status_impl as _get_block_status_impl,
    list_blocked_users_impl as _list_blocked_users_impl,
    unblock_user_impl as _unblock_user_impl,
)
from aos.api.social.follow import follow_impl as _follow_impl, unfollow_impl as _unfollow_impl
from aos.api.social.lists import (
    get_followers_impl as _get_followers_impl,
    get_following_impl as _get_following_impl,
    get_friends_impl as _get_friends_impl,
)
from aos.api.social.relationship import get_relationship_status_impl as _get_relationship_status_impl
from aos.api.social.search_users import search_users_impl as _search_users_impl
from aos.api.v1._transport import client_kwargs as _client_kwargs


@frappe.whitelist(methods=["POST"])
def follow(**kwargs):
    return _follow_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def unfollow(**kwargs):
    return _unfollow_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def get_relationship_status(**kwargs):
    return _get_relationship_status_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def get_following(**kwargs):
    return _get_following_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def get_followers(**kwargs):
    return _get_followers_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def get_friends(**kwargs):
    return _get_friends_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def search_users(**kwargs):
    return _search_users_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def block_user(**kwargs):
    return _block_user_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def unblock_user(**kwargs):
    return _unblock_user_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def get_block_status(**kwargs):
    return _get_block_status_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def list_blocked_users(**kwargs):
    return _list_blocked_users_impl(**_client_kwargs(kwargs))
