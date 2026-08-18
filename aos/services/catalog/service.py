"""Catalog taxonomy domain service."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe

from .constants import (
    ALLOWED_ATTRIBUTE_TYPES,
    ALLOWED_PRICE_TYPES,
    ALLOWED_PRICING_REQUIREMENTS,
    MAX_ATTRIBUTE_OPTIONS,
    MAX_CATEGORY_DEPTH,
    SELECT_ATTRIBUTE_TYPES,
)
from .errors import CatalogDataError, CatalogNotFoundError, CatalogValidationError
from .repository import CatalogRepository
from .validation import effective_attribute_field_type, normalize_category_id, split_choices


def attribute_key(attribute_name: str) -> str:
    try:
        return frappe.scrub(attribute_name or "")
    except Exception:
        return str(attribute_name or "").strip().lower().replace(" ", "_")


class CatalogService:
    def __init__(self, repository: CatalogRepository | None = None):
        self.repository = repository or CatalogRepository()

    def list_public_categories(self) -> list[dict[str, Any]]:
        rows = self.repository.load_categories()
        by_id = {str(row["name"]): row for row in rows}
        self._validate_graph_structure(by_id)
        public_ids = {
            category_id
            for category_id, row in by_id.items()
            if int(row.get("is_active") or 0) and self._has_public_ancestry(category_id, by_id)
        }
        items = {category_id: self._serialize_category(by_id[category_id]) for category_id in public_ids}
        roots: list[dict[str, Any]] = []
        for category_id in public_ids:
            item = items[category_id]
            parent_id = item.get("parent_id")
            if parent_id and parent_id in items:
                items[parent_id]["children"].append(item)
            elif not parent_id:
                roots.append(item)
        self._sort_tree(roots)
        return roots

    def get_public_schema(self, category: Any) -> dict[str, Any]:
        category_id = normalize_category_id(category)
        chain = self.get_category_chain(category_id, require_active=True)
        leaf = chain[0]
        attributes = resolve_attributes(chain)
        pricing = resolve_pricing(chain)
        return {
            "category": {
                "id": leaf["name"],
                "name": leaf["category_name"],
                "parent_id": leaf.get("parent"),
                "is_group": int(leaf.get("is_group") or 0),
                "is_service": int(leaf.get("is_service") or 0),
            },
            "attributes": attributes,
            "pricing": pricing,
        }

    def get_category_chain(self, category: Any, *, require_active: bool = False) -> list[dict[str, Any]]:
        category_id = normalize_category_id(category)
        rows = self.repository.load_categories()
        by_id = {str(row["name"]): dict(row) for row in rows}
        self._validate_graph_structure(by_id)
        current = category_id
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        while current:
            if current in seen:
                raise CatalogDataError("Catalog category cycle detected.")
            seen.add(current)
            row = by_id.get(current)
            if not row:
                if not chain:
                    raise CatalogNotFoundError("Category not found.")
                raise CatalogDataError("Catalog category parent is missing.")
            if require_active and not int(row.get("is_active") or 0):
                raise CatalogNotFoundError("Category not found.")
            row["parent"] = row.get("parent_aos_category") or None
            chain.append(row)
            if len(chain) > MAX_CATEGORY_DEPTH:
                raise CatalogDataError("Catalog category depth exceeded.")
            current = str(row.get("parent_aos_category") or "").strip()

        category_names = [str(row["name"]) for row in chain]
        attribute_rows = self.repository.load_category_attribute_rows(category_names)
        rows_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in attribute_rows:
            rows_by_parent[str(row.get("parent") or "")].append(row)
        attribute_names = [str(row.get("attribute") or "") for row in attribute_rows if row.get("attribute")]
        definitions = self.repository.load_attributes(attribute_names)
        expected_attributes = {name for name in attribute_names if name}
        if expected_attributes.difference(definitions):
            raise CatalogDataError("Catalog attribute definition is missing.")
        for row in chain:
            row["attributes"] = rows_by_parent.get(str(row["name"]), [])
            row["attribute_definitions"] = definitions
        return chain

    def get_sellable_category_chain(self, category: Any) -> list[dict[str, Any]]:
        category_id = normalize_category_id(category)
        chain = self.get_category_chain(category_id, require_active=True)
        if int(chain[0].get("is_group") or 0):
            raise CatalogValidationError("Select a sellable leaf category.", code="CATEGORY_NOT_SELLABLE")
        return chain

    def assert_sellable_category(self, category: Any) -> str:
        return str(self.get_sellable_category_chain(category)[0]["name"])

    def resolve_filter_values(self, category: Any) -> list[str]:
        try:
            category_id = normalize_category_id(category)
        except CatalogValidationError:
            return []
        rows = self.repository.load_categories()
        by_id = {str(row["name"]): row for row in rows}
        self._validate_graph_structure(by_id)
        row = by_id.get(category_id)
        if not row or not int(row.get("is_active") or 0) or not self._has_public_ancestry(category_id, by_id):
            return []
        if not int(row.get("is_group") or 0):
            return [category_id]
        children = [
            child
            for child in rows
            if child.get("parent_aos_category") == category_id
            and int(child.get("is_active") or 0)
            and not int(child.get("is_group") or 0)
        ]
        children.sort(key=self._sort_key)
        return [str(child["name"]) for child in children]

    @staticmethod
    def invalidate_cache() -> None:
        try:
            frappe.cache().delete_keys("aos:ads:cat_children:*")
        except Exception:
            try:
                frappe.logger("aos.catalog").warning("catalog_cache_invalidation_failed")
            except Exception:
                pass

    @staticmethod
    def _validate_graph_structure(by_id: dict[str, dict[str, Any]]) -> None:
        for category_id, row in by_id.items():
            parent_id = str(row.get("parent_aos_category") or "").strip()
            if not parent_id:
                continue
            parent = by_id.get(parent_id)
            if not parent:
                continue
            if int(row.get("is_group") or 0):
                raise CatalogDataError("Catalog group cannot have a parent.")
            if not int(parent.get("is_group") or 0):
                raise CatalogDataError("Catalog child parent is not a group.")
            if parent.get("parent_aos_category"):
                raise CatalogDataError("Catalog category depth exceeded.")
            if parent_id == category_id:
                raise CatalogDataError("Catalog category cycle detected.")

    def _has_public_ancestry(self, category_id: str, by_id: dict[str, dict[str, Any]]) -> bool:
        current = category_id
        seen: set[str] = set()
        depth = 0
        while current:
            if current in seen:
                raise CatalogDataError("Catalog category cycle detected.")
            seen.add(current)
            row = by_id.get(current)
            if not row:
                return False
            if not int(row.get("is_active") or 0):
                return False
            depth += 1
            if depth > MAX_CATEGORY_DEPTH:
                raise CatalogDataError("Catalog category depth exceeded.")
            current = str(row.get("parent_aos_category") or "").strip()
        return True

    @staticmethod
    def _serialize_category(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("name"),
            "name": row.get("category_name") or row.get("name"),
            "icon": row.get("icon") or "",
            "icon_media": row.get("icon_media") or None,
            "icon_media_id": row.get("icon_media") or None,
            "parent_id": row.get("parent_aos_category") or None,
            "sort_order": _database_sort_order(row.get("sort_order") or 0),
            "is_group": int(row.get("is_group") or 0),
            "children": [],
        }

    @staticmethod
    def _sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
        return (
            _database_sort_order(row.get("sort_order") or 0),
            str(row.get("category_name") or row.get("name") or "").casefold(),
            str(row.get("name") or ""),
        )

    def _sort_tree(self, nodes: list[dict[str, Any]]) -> None:
        nodes.sort(
            key=lambda row: (
                _database_sort_order(row.get("sort_order") or 0),
                str(row.get("name") or "").casefold(),
                str(row.get("id") or ""),
            )
        )
        for node in nodes:
            self._sort_tree(node["children"])



def _database_sort_order(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise CatalogDataError("Catalog sort order is invalid.") from exc
    if result < 0 or result > 1_000_000:
        raise CatalogDataError("Catalog sort order is invalid.")
    return result


def resolve_pricing(chain_leaf_to_root: list[dict[str, Any]]) -> dict[str, Any]:
    if not chain_leaf_to_root:
        raise CatalogDataError("Catalog category chain is empty.")
    leaf = chain_leaf_to_root[0]
    requirement = str(leaf.get("pricing_requirement") or "Optional").strip() or "Optional"
    if requirement not in ALLOWED_PRICING_REQUIREMENTS:
        raise CatalogDataError("Catalog pricing requirement is invalid.")
    try:
        allowed_types = split_choices(
            leaf.get("allowed_price_types"),
            field="price_type",
            allowed=ALLOWED_PRICE_TYPES,
        )
        allowed_units = split_choices(
            leaf.get("allowed_price_units"),
            field="price_unit",
            max_items=50,
        )
    except CatalogValidationError as exc:
        raise CatalogDataError("Catalog pricing configuration is invalid.") from exc
    pricing: dict[str, Any] = {"pricing_requirement": requirement}
    if allowed_types:
        pricing["allowed_price_types"] = allowed_types
    if int(leaf.get("is_service") or 0) and allowed_units:
        pricing["allowed_price_units"] = allowed_units
    return pricing


def resolve_attributes(chain_leaf_to_root: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve root-to-leaf overrides with the leaf row as the final authority."""

    by_attribute: dict[str, dict[str, Any]] = {}
    for category in reversed(chain_leaf_to_root):
        definitions = category.get("attribute_definitions") or {}
        # Legacy duplicates are resolved deterministically by child-table order;
        # document validation rejects new duplicates.
        rows = sorted(
            list(category.get("attributes") or []),
            key=lambda row: (int(row.get("idx") or 0), str(row.get("name") or "")),
        )
        for row in rows:
            attribute_name = str(row.get("attribute") or "").strip()
            if not attribute_name:
                continue
            if not int(row.get("is_active") or 0):
                by_attribute.pop(attribute_name, None)
                continue
            definition = definitions.get(attribute_name)
            if not definition or not int(definition.get("is_active") or 0):
                continue
            base_field_type = str(definition.get("field_type") or "Text").strip()
            if base_field_type not in ALLOWED_ATTRIBUTE_TYPES:
                raise CatalogDataError("Catalog attribute type is invalid.")
            try:
                override_options = split_choices(
                    row.get("options_override"),
                    field="attribute_option",
                    max_items=MAX_ATTRIBUTE_OPTIONS,
                )
                definition_options = split_choices(
                    definition.get("options"),
                    field="attribute_option",
                    max_items=MAX_ATTRIBUTE_OPTIONS,
                )
            except CatalogValidationError as exc:
                raise CatalogDataError("Catalog attribute options are invalid.") from exc
            field_type = effective_attribute_field_type(
                base_field_type,
                has_options_override=bool(override_options),
            )
            options = override_options or definition_options
            if options and field_type not in SELECT_ATTRIBUTE_TYPES:
                raise CatalogDataError("Catalog attribute options are invalid.")
            by_attribute[attribute_name] = {
                "id": attribute_name,
                "key": attribute_key(attribute_name),
                "label": definition.get("label") or attribute_name,
                "type": field_type,
                "required": int(row.get("is_required") or 0),
                "unit": definition.get("unit") or "",
                "help_text": definition.get("help_text") or "",
                "options": options,
                "sort_order": _database_sort_order(row.get("sort_order") or 0),
            }
    resolved = sorted(
        by_attribute.values(),
        key=lambda row: (
            _database_sort_order(row.get("sort_order") or 0),
            str(row.get("label") or "").casefold(),
            str(row.get("id") or ""),
        ),
    )
    keys: dict[str, str] = {}
    for attribute in resolved:
        key = str(attribute.get("key") or "")
        previous = keys.get(key)
        if previous and previous != attribute["id"]:
            raise CatalogDataError("Catalog attribute keys are ambiguous.")
        keys[key] = str(attribute["id"])
    return resolved
