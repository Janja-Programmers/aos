"""Stable public AOS Calls v1 endpoints.

The wrappers strip only Frappe-owned transport metadata, validate strict field
contracts and delegate to the established Calls implementation modules.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe

from aos.api.v1._transport import client_kwargs as _client_kwargs
from aos.services.calls.api import run_call_api
from aos.services.calls.endpoints import ENDPOINT_SPECS, TRANSACTIONAL_ENDPOINTS

from aos.api.calls.call import (
    initiate_call_impl as _initiate_call_impl,
    mark_call_ringing_impl as _mark_call_ringing_impl,
    accept_call_impl as _accept_call_impl,
    reject_call_impl as _reject_call_impl,
    cancel_call_impl as _cancel_call_impl,
    end_call_impl as _end_call_impl,
    add_call_participants_impl as _add_call_participants_impl,
    request_video_upgrade_impl as _request_video_upgrade_impl,
    respond_video_upgrade_impl as _respond_video_upgrade_impl,
)
from aos.api.calls.status import get_call_status_impl as _get_call_status_impl
from aos.api.calls.token import get_call_token_impl as _get_call_token_impl
from aos.api.calls.history import (
    list_calls_impl as _list_calls_impl,
    delete_call_logs_impl as _delete_call_logs_impl,
    clear_call_history_impl as _clear_call_history_impl,
)


def _call(name: str, implementation: Callable[..., dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
    return run_call_api(
        implementation,
        _client_kwargs(kwargs),
        spec=ENDPOINT_SPECS[name],
        operation_name=name,
        transactional=name in TRANSACTIONAL_ENDPOINTS,
    )


@frappe.whitelist(methods=["POST"])
def initiate_call(**kwargs):
    return _call("initiate_call", _initiate_call_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def mark_call_ringing(**kwargs):
    return _call("mark_call_ringing", _mark_call_ringing_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def accept_call(**kwargs):
    return _call("accept_call", _accept_call_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def reject_call(**kwargs):
    return _call("reject_call", _reject_call_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def cancel_call(**kwargs):
    return _call("cancel_call", _cancel_call_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def end_call(**kwargs):
    return _call("end_call", _end_call_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def add_call_participants(**kwargs):
    return _call("add_call_participants", _add_call_participants_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def request_video_upgrade(**kwargs):
    return _call("request_video_upgrade", _request_video_upgrade_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def respond_video_upgrade(**kwargs):
    return _call("respond_video_upgrade", _respond_video_upgrade_impl, kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_call_status(**kwargs):
    return _call("get_call_status", _get_call_status_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def get_call_token(**kwargs):
    return _call("get_call_token", _get_call_token_impl, kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_calls(**kwargs):
    return _call("list_calls", _list_calls_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_call_logs(**kwargs):
    return _call("delete_call_logs", _delete_call_logs_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def clear_call_history(**kwargs):
    return _call("clear_call_history", _clear_call_history_impl, kwargs)
