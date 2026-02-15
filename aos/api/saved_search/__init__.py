import frappe

from .save import save_search_impl
from .list import list_saved_searches_impl
from .delete import delete_saved_search_impl


@frappe.whitelist(methods=["POST"])
def save_search(**kwargs):
    return save_search_impl(**kwargs)


@frappe.whitelist()
def list_saved_searches(**kwargs):
    return list_saved_searches_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_saved_search(**kwargs):
    return delete_saved_search_impl(**kwargs)
