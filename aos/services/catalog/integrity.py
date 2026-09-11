"""Transactional integrity guards for low-volume Catalog administrator writes."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import MAX_ATTRIBUTE_OPTIONS, MAX_CATEGORIES
from .errors import CatalogConflictError, CatalogDataError, CatalogValidationError
from .validation import split_choices


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _doc_attribute_names(doc: Any | None) -> set[str]:
    if doc is None:
        return set()
    return {
        _clean(getattr(row, "attribute", ""))
        for row in list(getattr(doc, "attributes", None) or [])
        if _clean(getattr(row, "attribute", ""))
    }


def _lock_attribute_definitions(attribute_names: set[str]) -> None:
    names = sorted(name for name in attribute_names if name)
    if not names:
        return
    placeholders = ", ".join(["%s"] * len(names))
    frappe.db.sql(
        f"SELECT name FROM `tabAOS Ad Attribute` WHERE name IN ({placeholders}) "
        "ORDER BY name FOR UPDATE",
        tuple(names),
    )


def lock_category_mutation(doc: Any) -> None:
    """Lock category/parents, then every referenced attribute definition.

    Ads mutation validation uses the same category-first lock ordering. This
    serializes category relationship changes, reusable-attribute mutations and
    listing writes without relying on process-local locks.
    """

    existing = not bool(doc.is_new()) and _clean(getattr(doc, "name", ""))
    if existing:
        frappe.db.sql(
            "SELECT name FROM `tabAOS Category` WHERE name=%s FOR UPDATE",
            (doc.name,),
        )

    previous = doc.get_doc_before_save() if existing else None
    parents = {
        _clean(getattr(doc, "parent_aos_category", "")),
        _clean(getattr(previous, "parent_aos_category", "")) if previous else "",
    }
    names = sorted(name for name in parents if name and name != _clean(getattr(doc, "name", "")))
    if names:
        placeholders = ", ".join(["%s"] * len(names))
        frappe.db.sql(
            f"SELECT name FROM `tabAOS Category` WHERE name IN ({placeholders}) ORDER BY name FOR UPDATE",
            tuple(names),
        )

    _lock_attribute_definitions(
        _doc_attribute_names(doc) | _doc_attribute_names(previous)
    )


def lock_category_schema_for_ad(category_id: str) -> None:
    """Lock the complete reusable Catalog schema consumed by an Ads write."""

    category_id = _clean(category_id)
    if not category_id:
        return
    rows = frappe.db.sql(
        "SELECT name, parent_aos_category FROM `tabAOS Category` WHERE name=%s FOR UPDATE",
        (category_id,),
        as_dict=True,
    )
    if not rows:
        return
    parent = _clean(rows[0].get("parent_aos_category"))
    if parent:
        frappe.db.sql(
            "SELECT name FROM `tabAOS Category` WHERE name=%s FOR UPDATE",
            (parent,),
        )

    category_names = sorted({name for name in (category_id, parent) if name})
    placeholders = ", ".join(["%s"] * len(category_names))
    relation_rows = frappe.db.sql(
        f"""
        SELECT name, attribute
        FROM `tabAOS Category Attribute Row`
        WHERE parenttype='AOS Category'
          AND parentfield='attributes'
          AND parent IN ({placeholders})
        ORDER BY parent, name
        FOR UPDATE
        """,
        tuple(category_names),
        as_dict=True,
    )
    frappe.db.sql(
        f"""
        SELECT name
        FROM `tabAOS Category Attribute Dependency Row`
        WHERE parenttype='AOS Category'
          AND parentfield='attribute_dependencies'
          AND parent IN ({placeholders})
        ORDER BY parent, child_attribute, name
        FOR UPDATE
        """,
        tuple(category_names),
    )
    _lock_attribute_definitions(
        {_clean(row.get("attribute")) for row in relation_rows if _clean(row.get("attribute"))}
    )


def assert_category_transition_safe(doc: Any) -> None:
    if bool(doc.is_new()):
        return
    previous = doc.get_doc_before_save()
    if previous is None:
        return

    was_group = int(getattr(previous, "is_group", 0) or 0)
    is_group = int(getattr(doc, "is_group", 0) or 0)
    if was_group and not is_group and frappe.db.exists(
        "AOS Category", {"parent_aos_category": doc.name}
    ):
        raise CatalogValidationError(
            "A category with children cannot be changed into a leaf.",
            code="CATEGORY_IN_USE",
        )
    if not was_group and is_group and frappe.db.exists("AOS Ad", {"category": doc.name}):
        raise CatalogValidationError(
            "A category used by ads cannot be changed into a group.",
            code="CATEGORY_IN_USE",
        )


def _category_scope_has_ads(doc: Any) -> bool:
    if frappe.db.exists("AOS Ad", {"category": doc.name}):
        return True
    if not int(getattr(doc, "is_group", 0) or 0):
        previous = doc.get_doc_before_save()
        if not previous or not int(getattr(previous, "is_group", 0) or 0):
            return False
    children = frappe.get_all(
        "AOS Category",
        filters={"parent_aos_category": doc.name},
        pluck="name",
        limit=MAX_CATEGORIES + 1,
    )
    if len(children or []) > MAX_CATEGORIES:
        raise CatalogDataError("Catalog category limit exceeded.")
    return bool(children and frappe.db.exists("AOS Ad", {"category": ["in", children]}))


def _attribute_rows(doc: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in list(getattr(doc, "attributes", None) or []):
        attribute = _clean(getattr(row, "attribute", ""))
        if attribute:
            result[attribute] = row
    return result


def _dependency_rows_by_child(doc: Any) -> dict[str, set[tuple[str, str]]]:
    """Expand grouped persistence rows into stable logical dependency edges."""

    result: dict[str, set[tuple[str, str]]] = {}
    for row in list(getattr(doc, "attribute_dependencies", None) or []):
        child = _clean(getattr(row, "child_attribute", ""))
        parent_option = _clean(getattr(row, "parent_option", ""))
        if not child or not parent_option:
            continue
        child_options = split_choices(
            getattr(row, "child_options", None),
            field="child_option",
            max_items=MAX_ATTRIBUTE_OPTIONS,
        )
        for child_option in child_options:
            result.setdefault(child, set()).add((child_option, parent_option))
    return result


def assert_category_schema_change_safe(doc: Any) -> None:
    """Block destructive schema changes once Ads depend on the category scope.

    Sorting, labels, images, activation, and adding optional attributes remain
    mutable. Destructive inherited/leaf schema changes require a new category or
    attribute definition so existing Ads stay editable under a coherent schema.
    """

    if bool(doc.is_new()):
        return
    previous = doc.get_doc_before_save()
    if previous is None or not _category_scope_has_ads(doc):
        return

    if _clean(getattr(previous, "parent_aos_category", "")) != _clean(
        getattr(doc, "parent_aos_category", "")
    ):
        raise CatalogValidationError(
            "A category used by ads cannot be moved to a different schema parent.",
            code="CATEGORY_IN_USE",
        )

    if not int(getattr(doc, "is_group", 0) or 0):
        pricing_fields = ("is_service", "pricing_requirement", "allowed_price_types", "allowed_price_units")
        if any(_clean(getattr(previous, field, "")) != _clean(getattr(doc, field, "")) for field in pricing_fields):
            raise CatalogValidationError(
                "Pricing configuration for a category used by ads cannot be changed destructively.",
                code="CATEGORY_IN_USE",
            )

    before = _attribute_rows(previous)
    after = _attribute_rows(doc)
    for attribute, old_row in before.items():
        new_row = after.get(attribute)
        old_active = int(getattr(old_row, "is_active", 0) or 0)
        if new_row is None:
            if old_active:
                raise CatalogValidationError(
                    "Active attributes used by an ad category cannot be removed.",
                    code="CATEGORY_IN_USE",
                )
            continue
        if old_active != int(getattr(new_row, "is_active", 0) or 0):
            raise CatalogValidationError(
                "Attribute activation for a category used by ads cannot be changed destructively.",
                code="CATEGORY_IN_USE",
            )
        if int(getattr(old_row, "is_required", 0) or 0) != int(getattr(new_row, "is_required", 0) or 0):
            raise CatalogValidationError(
                "Required attributes for a category used by ads cannot be changed destructively.",
                code="CATEGORY_IN_USE",
            )
        if _clean(getattr(old_row, "depends_on_attribute", "")) != _clean(
            getattr(new_row, "depends_on_attribute", "")
        ):
            raise CatalogValidationError(
                "Attribute dependencies for a category used by ads cannot be changed in place.",
                code="CATEGORY_IN_USE",
            )
        old_override = split_choices(
            getattr(old_row, "options_override", None),
            field="attribute_option",
            max_items=MAX_ATTRIBUTE_OPTIONS,
        )
        new_override = split_choices(
            getattr(new_row, "options_override", None),
            field="attribute_option",
            max_items=MAX_ATTRIBUTE_OPTIONS,
        )
        if old_override != new_override:
            definition_options = split_choices(
                frappe.db.get_value("AOS Ad Attribute", attribute, "options") or "",
                field="attribute_option",
                max_items=MAX_ATTRIBUTE_OPTIONS,
            )
            old_effective = old_override or definition_options
            new_effective = new_override or definition_options
            if not set(old_effective).issubset(set(new_effective)):
                raise CatalogValidationError(
                    "Category attribute choices used by ads cannot be narrowed.",
                    code="CATEGORY_IN_USE",
                )

    before_dependencies = _dependency_rows_by_child(previous)
    after_dependencies = _dependency_rows_by_child(doc)
    for attribute in before:
        if not before_dependencies.get(attribute, set()).issubset(
            after_dependencies.get(attribute, set())
        ):
            raise CatalogValidationError(
                "Existing attribute option dependency mappings for a category used by ads cannot be removed or changed.",
                code="CATEGORY_IN_USE",
            )

    for attribute, row in after.items():
        if attribute in before:
            continue
        if int(getattr(row, "is_active", 0) or 0) and int(getattr(row, "is_required", 0) or 0):
            raise CatalogValidationError(
                "New attributes on a category with ads must be optional.",
                code="CATEGORY_IN_USE",
            )


def assert_category_delete_safe(category_id: str) -> None:
    category_id = _clean(category_id)
    if frappe.db.exists("AOS Category", {"parent_aos_category": category_id}):
        raise CatalogValidationError(
            "Delete or move child categories before deleting this category.",
            code="CATEGORY_IN_USE",
        )
    if frappe.db.exists("AOS Ad", {"category": category_id}):
        raise CatalogValidationError(
            "Deactivate categories that are already referenced by ads.",
            code="CATEGORY_IN_USE",
        )


def lock_attribute_mutation(doc: Any) -> None:
    if bool(doc.is_new()) or not _clean(getattr(doc, "name", "")):
        return
    frappe.db.sql(
        "SELECT name FROM `tabAOS Ad Attribute` WHERE name = %s FOR UPDATE",
        (doc.name,),
    )


def assert_attribute_identity_immutable(doc: Any) -> None:
    if bool(doc.is_new()):
        return
    previous = doc.get_doc_before_save()
    if previous is None:
        return
    previous_key = _clean(getattr(previous, "attribute_key", ""))
    current_key = _clean(getattr(doc, "attribute_key", ""))
    if previous_key and current_key and previous_key != current_key:
        raise CatalogConflictError(
            "Attribute key is immutable after creation.",
            code="ATTRIBUTE_IDENTITY_IMMUTABLE",
        )
    if previous_key:
        doc.attribute_key = previous_key


def assert_attribute_schema_change_safe(doc: Any) -> None:
    """Prevent destructive attribute changes once Ads or dependencies reference it."""

    if bool(doc.is_new()):
        return
    previous = doc.get_doc_before_save()
    if previous is None:
        return

    used_by_ads = bool(frappe.db.exists("AOS Ad Attribute Value", {"attribute": doc.name}))
    dependency_parent = bool(
        frappe.db.exists("AOS Category Attribute Row", {"depends_on_attribute": doc.name})
    )
    dependency_child = bool(
        frappe.db.exists("AOS Category Attribute Dependency Row", {"child_attribute": doc.name})
    )
    dependency_referenced = dependency_parent or dependency_child
    if not used_by_ads and not dependency_referenced:
        return

    if dependency_referenced and int(getattr(previous, "is_active", 0) or 0) != int(
        getattr(doc, "is_active", 0) or 0
    ):
        raise CatalogValidationError(
            "An attribute used by dependency rules cannot be deactivated in place.",
            code="ATTRIBUTE_IN_USE",
        )

    previous_type = _clean(getattr(previous, "field_type", ""))
    current_type = _clean(getattr(doc, "field_type", ""))
    if previous_type != current_type:
        raise CatalogValidationError(
            "Create a new attribute instead of changing the type of an attribute already in use.",
            code="ATTRIBUTE_IN_USE",
        )

    if current_type not in {"Select", "MultiSelect"}:
        return
    previous_options = set(
        split_choices(
            getattr(previous, "options", None),
            field="attribute_option",
            max_items=MAX_ATTRIBUTE_OPTIONS,
        )
    )
    current_options = set(
        split_choices(
            getattr(doc, "options", None),
            field="attribute_option",
            max_items=MAX_ATTRIBUTE_OPTIONS,
        )
    )
    if not previous_options.issubset(current_options):
        raise CatalogValidationError(
            "Options referenced by existing ads or dependency rules cannot be removed.",
            code="ATTRIBUTE_IN_USE",
        )
    if dependency_referenced and previous_options != current_options:
        raise CatalogValidationError(
            "Global options for an attribute used by dependency rules cannot change in place; update category-specific options and mappings atomically instead.",
            code="ATTRIBUTE_IN_USE",
        )


def assert_attribute_delete_safe(attribute_id: str) -> None:
    attribute_id = _clean(attribute_id)
    if frappe.db.exists("AOS Category Attribute Row", {"attribute": attribute_id}):
        raise CatalogValidationError(
            "Remove the attribute from categories before deleting it.",
            code="ATTRIBUTE_IN_USE",
        )
    if frappe.db.exists("AOS Ad Attribute Value", {"attribute": attribute_id}):
        raise CatalogValidationError(
            "Deactivate attributes that are already referenced by ads.",
            code="ATTRIBUTE_IN_USE",
        )
