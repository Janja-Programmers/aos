"""Deterministic Catalog pricing, attribute and dependency resolution.

This module contains pure read-model resolution. Persistence, caching and Media
projection remain in ``CatalogService``; document mutation validation remains
in ``validation``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .constants import (
    ALLOWED_ATTRIBUTE_TYPES,
    ALLOWED_PRICE_TYPES,
    ALLOWED_PRICING_REQUIREMENTS,
    MAX_ATTRIBUTE_OPTIONS,
    SELECT_ATTRIBUTE_TYPES,
)
from .errors import CatalogDataError, CatalogValidationError
from .validation import canonical_attribute_key, split_choices


def attribute_key(attribute_name: Any) -> str:
    """Return the canonical key derivation used by active Ads snapshot fallbacks."""
    if attribute_name in (None, ""):
        return ""
    return canonical_attribute_key(attribute_name)


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


def resolve_attributes(
    chain_leaf_to_root: list[dict[str, Any]], *, include_dependency_map: bool = False
) -> list[dict[str, Any]]:
    """Resolve root-to-leaf attributes and their direct option dependencies.

    A leaf relationship overrides the same root relationship completely,
    including dependency configuration and dependency mappings. Dependency
    chains may be multi-level, but each attribute has at most one direct parent.
    """

    by_attribute: dict[str, dict[str, Any]] = {}
    for category in reversed(chain_leaf_to_root):
        category_id = str(category.get("name") or "").strip()
        definitions = category.get("attribute_definitions") or {}
        rows = sorted(
            list(category.get("attributes") or []),
            key=lambda row: (
                _database_sort_order(row.get("sort_order") or 0),
                int(row.get("idx") or 0),
                str(row.get("name") or ""),
            ),
        )
        seen_in_category: set[str] = set()
        for row in rows:
            attribute_name = str(row.get("attribute") or "").strip()
            if not attribute_name:
                raise CatalogDataError("Catalog attribute relation is invalid.")
            if attribute_name in seen_in_category:
                raise CatalogDataError("Catalog category contains duplicate attribute relations.")
            seen_in_category.add(attribute_name)
            if not int(row.get("is_active") or 0):
                by_attribute.pop(attribute_name, None)
                continue
            definition = definitions.get(attribute_name)
            if not definition or not int(definition.get("is_active") or 0):
                continue
            field_type = str(definition.get("field_type") or "").strip()
            if field_type not in ALLOWED_ATTRIBUTE_TYPES:
                raise CatalogDataError("Catalog attribute type is invalid.")
            attribute_key_value = str(definition.get("attribute_key") or "").strip()
            if not attribute_key_value:
                raise CatalogDataError("Catalog attribute key is missing.")
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
            if override_options and field_type not in SELECT_ATTRIBUTE_TYPES:
                raise CatalogDataError("Catalog attribute options are invalid.")
            options = override_options or definition_options
            if options and field_type not in SELECT_ATTRIBUTE_TYPES:
                raise CatalogDataError("Catalog attribute options are invalid.")
            by_attribute[attribute_name] = {
                "id": attribute_name,
                "key": attribute_key_value,
                "label": definition.get("label") or attribute_name,
                "type": field_type,
                "required": int(row.get("is_required") or 0),
                "unit": definition.get("unit") or "",
                "help_text": definition.get("help_text") or "",
                "options": options,
                "sort_order": _database_sort_order(row.get("sort_order") or 0),
                "_source_category": category_id,
                "_depends_on_attribute": str(row.get("depends_on_attribute") or "").strip(),
            }

    _apply_attribute_dependencies(
        by_attribute,
        chain_leaf_to_root,
        include_dependency_map=include_dependency_map,
    )
    resolved = _sort_resolved_attributes(by_attribute)
    keys: dict[str, str] = {}
    for attribute in resolved:
        key = str(attribute.get("key") or "")
        previous = keys.get(key)
        if previous and previous != attribute["id"]:
            raise CatalogDataError("Catalog attribute keys are ambiguous.")
        keys[key] = str(attribute["id"])
        attribute.pop("_source_category", None)
        attribute.pop("_depends_on_attribute", None)
    return resolved



def _attribute_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (
        _database_sort_order(row.get("sort_order") or 0),
        str(row.get("label") or "").casefold(),
        str(row.get("id") or ""),
    )


def _sort_resolved_attributes(by_attribute: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable topological order: dependency parents always precede children."""

    indegree = {attribute_id: 0 for attribute_id in by_attribute}
    children: dict[str, list[str]] = defaultdict(list)
    for attribute_id, item in by_attribute.items():
        dependency = item.get("depends_on") or {}
        parent_id = str(dependency.get("id") or "")
        if not parent_id:
            continue
        if parent_id not in by_attribute:
            raise CatalogDataError("Catalog dependency parent attribute is missing.")
        indegree[attribute_id] += 1
        children[parent_id].append(attribute_id)

    ready = sorted(
        (by_attribute[attribute_id] for attribute_id, count in indegree.items() if count == 0),
        key=_attribute_sort_key,
    )
    result: list[dict[str, Any]] = []
    while ready:
        item = ready.pop(0)
        result.append(item)
        for child_id in children.get(str(item["id"]), []):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(by_attribute[child_id])
                ready.sort(key=_attribute_sort_key)
    if len(result) != len(by_attribute):
        raise CatalogDataError("Catalog attribute dependency cycle detected.")
    return result

def _apply_attribute_dependencies(
    by_attribute: dict[str, dict[str, Any]],
    chain_leaf_to_root: list[dict[str, Any]],
    *,
    include_dependency_map: bool,
) -> None:
    dependencies_by_category: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for category in chain_leaf_to_root:
        category_id = str(category.get("name") or "").strip()
        for row in list(category.get("attribute_dependencies") or []):
            child_attribute = str(row.get("child_attribute") or "").strip()
            if not child_attribute:
                raise CatalogDataError("Catalog attribute dependency is invalid.")
            dependencies_by_category[category_id][child_attribute].append(row)

    graph: dict[str, str] = {}
    for attribute_id, item in by_attribute.items():
        parent_id = str(item.get("_depends_on_attribute") or "").strip()
        source_category = str(item.get("_source_category") or "").strip()
        mapping_rows = dependencies_by_category[source_category].get(attribute_id, [])
        if not parent_id:
            if mapping_rows:
                raise CatalogDataError("Catalog dependency mappings have no parent attribute.")
            continue
        if parent_id == attribute_id:
            raise CatalogDataError("Catalog attribute dependency cycle detected.")
        parent = by_attribute.get(parent_id)
        if not parent:
            raise CatalogDataError("Catalog dependency parent attribute is missing.")
        if item.get("type") != "Select" or parent.get("type") != "Select":
            raise CatalogDataError("Catalog dependencies require Select attributes.")
        child_options = list(item.get("options") or [])
        parent_options = list(parent.get("options") or [])
        if not child_options or not parent_options:
            raise CatalogDataError("Catalog dependent attributes require canonical options.")
        if int(item.get("required") or 0) and not int(parent.get("required") or 0):
            raise CatalogDataError("A required dependent attribute requires its parent attribute.")
        if not mapping_rows:
            raise CatalogDataError("Catalog dependent attribute has no option mappings.")

        allowed_children = set(child_options)
        allowed_parents = set(parent_options)
        seen_pairs: set[tuple[str, str]] = set()
        covered_children: set[str] = set()
        by_parent: dict[str, set[str]] = defaultdict(set)
        for row in mapping_rows:
            child_option = str(row.get("child_option") or "").strip()
            parent_option = str(row.get("parent_option") or "").strip()
            if child_option not in allowed_children or parent_option not in allowed_parents:
                raise CatalogDataError("Catalog dependency option mapping is invalid.")
            pair = (child_option, parent_option)
            if pair in seen_pairs:
                raise CatalogDataError("Catalog contains duplicate dependency mappings.")
            seen_pairs.add(pair)
            covered_children.add(child_option)
            by_parent[parent_option].add(child_option)
        if covered_children != allowed_children:
            raise CatalogDataError("Every dependent option must map to a parent option.")
        if int(item.get("required") or 0) and set(by_parent) != allowed_parents:
            raise CatalogDataError(
                "Every parent option must provide an option for a required dependent attribute."
            )

        item["depends_on"] = {"id": parent_id, "key": parent["key"]}
        if include_dependency_map:
            item["_dependency_options"] = {
                parent_option: [option for option in child_options if option in children]
                for parent_option, children in by_parent.items()
            }
        graph[attribute_id] = parent_id

    for start in graph:
        seen: set[str] = set()
        current = start
        while current in graph:
            if current in seen:
                raise CatalogDataError("Catalog attribute dependency cycle detected.")
            seen.add(current)
            current = graph[current]
