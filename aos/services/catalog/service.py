"""Catalog taxonomy domain service.

Catalog is authoritative for category hierarchy, reusable attribute definitions,
and category-specific attribute configuration. Public projections are bounded,
deterministic, cacheable, and deliberately omit persistence/storage internals.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe

from aos.services.media.media_service import MediaService

from .cache import (
    clear_catalog_cache,
    get_category_schema_cache,
    get_category_tree_cache,
    set_category_schema_cache,
    set_category_tree_cache,
)
from .constants import (
    ALLOWED_ATTRIBUTE_TYPES,
    ALLOWED_PRICE_TYPES,
    ALLOWED_PRICING_REQUIREMENTS,
    MAX_ATTRIBUTE_OPTIONS,
    MAX_CATEGORY_DEPTH,
    SELECT_ATTRIBUTE_TYPES,
)
from .errors import CatalogDataError, CatalogNotFoundError, CatalogValidationError
from .integrity import lock_category_schema_for_ad
from .repository import CatalogRepository
from .validation import canonical_attribute_key, normalize_category_id, split_choices


def attribute_key(attribute_name: Any) -> str:
    """Return the canonical key derivation used by active Ads snapshot fallbacks."""
    if attribute_name in (None, ""):
        return ""
    return canonical_attribute_key(attribute_name)


class CatalogService:
    def __init__(
        self,
        repository: CatalogRepository | None = None,
        *,
        media_service: MediaService | None = None,
        use_cache: bool | None = None,
    ):
        self.repository = repository or CatalogRepository()
        self._media_service = media_service
        self._use_cache = (repository is None) if use_cache is None else bool(use_cache)

    def list_public_categories(self) -> list[dict[str, Any]]:
        if self._use_cache:
            cached = get_category_tree_cache()
            if cached is not None:
                return cached

        rows = self.repository.load_categories()
        by_id = {str(row["name"]): dict(row) for row in rows}
        self._validate_graph_structure(by_id)
        public_ids = {
            category_id
            for category_id in by_id
            if self._has_public_ancestry(category_id, by_id)
        }
        media_urls = self._public_category_image_urls(
            (by_id[category_id].get("image_media"), category_id) for category_id in public_ids
        )
        items = {
            category_id: self._serialize_category(
                by_id[category_id], category_id=category_id, media_urls=media_urls
            )
            for category_id in public_ids
        }
        roots: list[dict[str, Any]] = []
        for category_id in public_ids:
            item = items[category_id]
            parent_id = item.get("parent_id")
            if parent_id and parent_id in items:
                items[parent_id]["children"].append(item)
            elif not parent_id:
                roots.append(item)
        self._sort_tree(roots)

        if self._use_cache:
            set_category_tree_cache(roots)
        return roots

    def get_public_schema(self, category: Any) -> dict[str, Any]:
        category_id = normalize_category_id(category)
        if self._use_cache:
            cached = get_category_schema_cache(category_id)
            if cached is not None:
                return cached

        chain = self.get_category_chain(category_id, require_active=True)
        leaf = chain[0]
        resolved_pricing = resolve_pricing(chain)
        media_urls = self._public_category_image_urls(
            [(leaf.get("image_media"), str(leaf["name"]))]
        )
        image_media = str(leaf.get("image_media") or "").strip()
        schema = {
            "category": {
                "id": leaf["name"],
                "name": leaf["category_name"],
                "parent_id": leaf.get("parent"),
                "is_group": int(leaf.get("is_group") or 0),
                "is_service": int(leaf.get("is_service") or 0),
                "image_url": media_urls.get((image_media, str(leaf["name"]))) or None,
            },
            "attributes": resolve_attributes(chain),
            "pricing": {
                "requirement": resolved_pricing["pricing_requirement"],
                "allowed_price_types": resolved_pricing.get("allowed_price_types", []),
                "allowed_units": resolved_pricing.get("allowed_price_units", []),
            },
        }
        if self._use_cache:
            set_category_schema_cache(category_id, schema)
        return schema

    def get_category_chain(self, category: Any, *, require_active: bool = False) -> list[dict[str, Any]]:
        """Load one bounded schema chain using indexed category identity lookups."""

        category_id = normalize_category_id(category)
        current = category_id
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        while current:
            if current in seen:
                raise CatalogDataError("Catalog category cycle detected.")
            seen.add(current)
            row = self.repository.load_category(current)
            if not row:
                if not chain:
                    raise CatalogNotFoundError("Category not found.")
                raise CatalogDataError("Catalog category parent is missing.")
            if require_active and not int(row.get("is_active") or 0):
                raise CatalogNotFoundError("Category not found.")
            parent = str(row.get("parent_aos_category") or "").strip()
            if int(row.get("is_group") or 0) and parent:
                raise CatalogDataError("Catalog group cannot have a parent.")
            row["parent"] = parent or None
            chain.append(row)
            if len(chain) > MAX_CATEGORY_DEPTH:
                raise CatalogDataError("Catalog category depth exceeded.")
            current = parent

        if len(chain) == 2:
            parent = chain[1]
            if not int(parent.get("is_group") or 0) or parent.get("parent_aos_category"):
                raise CatalogDataError("Catalog child parent is not a root group.")

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

    def get_sellable_category_chain(
        self, category: Any, *, for_update: bool = False
    ) -> list[dict[str, Any]]:
        category_id = normalize_category_id(category)
        if for_update:
            lock_category_schema_for_ad(category_id)
        chain = self.get_category_chain(category_id, require_active=True)
        if int(chain[0].get("is_group") or 0):
            raise CatalogValidationError("Select a sellable leaf category.", code="CATEGORY_NOT_SELLABLE")
        return chain

    def assert_sellable_category(self, category: Any, *, for_update: bool = False) -> str:
        return str(
            self.get_sellable_category_chain(category, for_update=for_update)[0]["name"]
        )

    def resolve_filter_values(self, category: Any) -> list[str]:
        try:
            category_id = normalize_category_id(category)
        except CatalogValidationError:
            return []
        rows = self.repository.load_categories()
        by_id = {str(row["name"]): dict(row) for row in rows}
        self._validate_graph_structure(by_id)
        row = by_id.get(category_id)
        if not row or not self._has_public_ancestry(category_id, by_id):
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
        """Compatibility-free public cache invalidation entry point for callers."""
        clear_catalog_cache()

    @staticmethod
    def _validate_graph_structure(by_id: dict[str, dict[str, Any]]) -> None:
        for category_id, row in by_id.items():
            parent_id = str(row.get("parent_aos_category") or "").strip()
            if int(row.get("is_group") or 0) and parent_id:
                raise CatalogDataError("Catalog group cannot have a parent.")
            if not parent_id:
                continue
            if parent_id == category_id:
                raise CatalogDataError("Catalog category cycle detected.")
            parent = by_id.get(parent_id)
            if not parent:
                continue
            if not int(parent.get("is_group") or 0):
                raise CatalogDataError("Catalog child parent is not a group.")
            if parent.get("parent_aos_category"):
                raise CatalogDataError("Catalog category depth exceeded.")

    def _has_public_ancestry(self, category_id: str, by_id: dict[str, dict[str, Any]]) -> bool:
        current = category_id
        seen: set[str] = set()
        depth = 0
        while current:
            if current in seen:
                raise CatalogDataError("Catalog category cycle detected.")
            seen.add(current)
            row = by_id.get(current)
            if not row or not int(row.get("is_active") or 0):
                return False
            depth += 1
            if depth > MAX_CATEGORY_DEPTH:
                raise CatalogDataError("Catalog category depth exceeded.")
            current = str(row.get("parent_aos_category") or "").strip()
        return True

    def _public_category_image_urls(self, attachments) -> dict[tuple[str, str], str]:
        expected = sorted(
            {
                (str(media_id or "").strip(), str(category_id or "").strip())
                for media_id, category_id in attachments
                if str(media_id or "").strip() and str(category_id or "").strip()
            }
        )
        if not expected:
            return {}
        try:
            service = self._media_service or MediaService()
            return service.get_public_attachment_url_map(
                expected,
                purpose="category_icon",
                attached_doctype="AOS Category",
                attached_field="image_media",
            )
        except Exception:
            try:
                frappe.logger("aos.catalog", allow_site=True).warning("catalog_image_projection_failed")
            except Exception:
                pass
            return {}

    @staticmethod
    def _serialize_category(
        row: dict[str, Any],
        *,
        category_id: str,
        media_urls: dict[tuple[str, str], str],
    ) -> dict[str, Any]:
        media_id = str(row.get("image_media") or "").strip()
        return {
            "id": row.get("name"),
            "name": row.get("category_name") or row.get("name"),
            "image_url": media_urls.get((media_id, category_id)) or None,
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
    """Resolve root-to-leaf category rows with the leaf row as final authority."""

    by_attribute: dict[str, dict[str, Any]] = {}
    for category in reversed(chain_leaf_to_root):
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
            attribute_key = str(definition.get("attribute_key") or "").strip()
            if not attribute_key:
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
                "key": attribute_key,
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
