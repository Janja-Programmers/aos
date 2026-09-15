"""Canonical frontend Saved Search API v1 boundary."""
from __future__ import annotations
import frappe
from aos.api.shared.transport import execute_endpoint
from aos.api.saved_search.create import create_saved_search_impl
from aos.api.saved_search.update import update_saved_search_impl
from aos.api.saved_search.list import list_saved_searches_impl
from aos.api.saved_search.delete import delete_saved_search_impl

@frappe.whitelist(methods=["POST"])
def create_saved_search(**kwargs): return execute_endpoint(create_saved_search_impl,kwargs)
@frappe.whitelist(methods=["POST"])
def update_saved_search(**kwargs): return execute_endpoint(update_saved_search_impl,kwargs)
@frappe.whitelist(methods=["GET"])
def list_saved_searches(**kwargs): return execute_endpoint(list_saved_searches_impl,kwargs)
@frappe.whitelist(methods=["POST"])
def delete_saved_search(**kwargs): return execute_endpoint(delete_saved_search_impl,kwargs)
