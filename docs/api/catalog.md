# Catalog API v1

Catalog endpoints are guest-readable and return the standard AOS envelope. They do not expose seller identity, ownership, moderation notes, storage keys, private Media URLs, or raw Frappe documents.

## Get categories

`aos.api.v1.catalog.get_categories`

Authentication: guest allowed. Rate limit: bounded per source IP before database work.

Response `data` is a deterministic active tree:

```json
[
  {
    "id": "Electronics",
    "name": "Electronics",
    "icon": "https://public-media.example/category.png",
    "icon_media": "MEDIA-...",
    "icon_media_id": "MEDIA-...",
    "parent_id": null,
    "sort_order": 10,
    "is_group": 1,
    "children": []
  }
]
```

Only categories whose complete ancestry is active are returned. Orphans are hidden. Invalid cycles, depth, or parent/group relationships fail closed rather than returning a partial unsafe tree.

## Get category schema

`aos.api.v1.catalog.get_category_schema`

Input:

```json
{"category": "Mobile Phones"}
```

The category value must be a scalar string. The endpoint returns the resolved root-to-leaf attribute and pricing schema:

```json
{
  "category": {
    "id": "Mobile Phones",
    "name": "Mobile Phones",
    "parent_id": "Electronics",
    "is_group": 0,
    "is_service": 0
  },
  "attributes": [
    {
      "id": "Brand",
      "key": "brand",
      "label": "Brand",
      "type": "Select",
      "unit": "",
      "help_text": "",
      "required": 1,
      "sort_order": 10,
      "options": ["Example"]
    }
  ],
  "pricing": {
    "requirement": "Required",
    "allowed_price_types": ["Fixed", "Negotiable"],
    "allowed_units": []
  }
}
```

Inactive categories or inactive ancestors are indistinguishable from missing categories. Schema reads are bounded to one category query, one child-row query, and one attribute-definition query.

## Public error identifiers

| Error | HTTP status | Meaning |
| --- | ---: | --- |
| `VALIDATION_ERROR` | 400 | Required compatibility input is missing |
| `INVALID_CATALOG_INPUT` | 422 | Input type or scalar normalization is invalid |
| `INVALID_CATEGORY` | 422 | Category value is invalid |
| `INVALID_CATEGORY_SCHEMA` | 422 | Administrative schema configuration is invalid |
| `INVALID_CATEGORY_TREE` | 422 | Administrative hierarchy is invalid |
| `CATEGORY_NOT_SELLABLE` | 422 | Ads attempted to use a group/non-sellable category |
| `CATEGORY_NOT_FOUND` | 404 | Category is missing or not public |
| `CATALOG_DATA_ERROR` | 500 | Stored Catalog data is internally inconsistent |
| `INTERNAL_ERROR` | 500 | Unexpected internal failure |

Server-side exception text, SQL details, paths, private URLs, and tokens are never used as public messages.

## Compatibility

The success shapes and established category/media identifier fields are retained. Ads compatibility helpers remain importable but delegate to `CatalogService`. New mutation aliases were not added because the repository exposes Catalog administration through Frappe Desk only.
