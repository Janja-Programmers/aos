# Catalog

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_categories` | GET | Guest allowed | Client |
| `get_category_schema` | GET | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

Catalog is the canonical marketplace taxonomy and listing-schema domain. It owns category identity and hierarchy, reusable attribute definitions, category-specific attribute configuration, and the pricing/schema metadata that Ads consumes. Catalog does not own listing attribute values, user-generated media, object-storage configuration, or provider credentials.

The production design is intentionally small: categories form a maximum two-level root-group → leaf hierarchy (with a root leaf also allowed), attributes are reusable definitions, and `AOS Category Attribute Row` applies an attribute to a category. This bounded model replaces the need for nested-set bookkeeping and makes hierarchy validation, locking, traversal, and indexing deterministic.

## Responsibilities and boundaries

Catalog is the single source of truth for:

- stable category IDs, display names, parent relationships, activation and ordering;
- whether a category is a grouping node or sellable leaf;
- service/pricing rules used by Ads validation;
- reusable attribute identity, type, label, unit, help text and canonical options;
- category-specific attribute activation, requirement, order and select-option overrides;
- the authoritative category image relationship through Media.

Ads stores listing values and immutable display/type/unit snapshots for historical listing rendering, but Ads does not define the category or attribute schema. Ads create/update validation calls `CatalogService` and takes Catalog row locks before using a sellable schema. Public clients must use the Catalog endpoints rather than inspect Frappe DocTypes.

Catalog does **not** currently model numeric min/max constraints, arbitrary string-length constraints, or search/filter flags on attributes. Frontends must not invent those fields. If those capabilities are added later, their definitions belong here in Catalog rather than Ads.

## Dependency on Media

Category images use the existing canonical Media purpose `category_icon`. Its policy is defined only by Media and currently permits public JPEG, PNG and WebP images up to 5 MiB with dimensions from 16×16 through 4096×4096. Media remains authoritative for MIME/magic-byte validation, dimensions, visibility, object identity, storage location, upload confirmation, attachment limits and cleanup.

Catalog contains no MinIO/S3 bucket, object-key, credential, `File`, Attach-field or direct provider implementation. The category stores only `image_media`, a Link to `AOS Media Object`. It never persists the resolved URL as category truth.

`owner_user` on the Media object records the administrator who initialized the upload for audit/manage purposes. Resource authority is the Media attachment to the `AOS Category`, and the `category_icon` purpose requires `AOS Category` write permission. Replacement/removal can therefore safely release an old administrator-uploaded object through the trusted resource lifecycle without treating category content as ordinary seller-owned media.

## Data model

### `AOS Category`

A stable marketplace category. `allow_rename` is disabled; the document `name` is generated from the initial `category_name` and remains the canonical ID even if the display name is later edited.

| Field | Purpose |
|---|---|
| `category_name` | Human-readable category label. Required and unique. The initial value creates the stable document ID. |
| `parent_aos_category` | Optional Link to the root `AOS Category` group. Leaf-only; a group cannot have a parent. |
| `sort_order` | Non-negative deterministic order, bounded to 1,000,000. Name/ID are tie-breakers. |
| `is_group` | Marks a non-sellable root grouping category. |
| `is_active` | Controls public availability. An inactive ancestor hides its children from public reads and Ads validation. |
| `is_service` | Marks a sellable service category and permits price units. |
| `pricing_requirement` | `Required`, `Optional`, or `Hidden`; interpreted by Ads pricing validation. |
| `allowed_price_types` | Canonical newline-delimited allowlist from `Fixed`, `Negotiable`, `Contact for price`, `Free`. |
| `allowed_price_units` | Canonical newline-delimited service-unit choices. Empty for non-service categories. |
| `attributes` | Child table of category-specific attribute relationships. |
| `image_media` | Hidden/read-only canonical `AOS Media Object` link for the category image. |
| `icon_preview` | Desk-only HTML control. It does not store image authority. |

Layout fields such as Section Break/Column Break have no domain semantics. The old nested-set `lft`, `rgt` and tree-script contract is not part of the current model.

### `AOS Ad Attribute`

Reusable Catalog attribute definition. `allow_rename` is disabled, so the document `name` created from the initial `label` is a stable definition ID.

| Field | Purpose |
|---|---|
| `label` | Human-readable attribute label. Required and unique. |
| `attribute_key` | Immutable, unique, read-only client key generated on creation from the initial label. Public schemas use this key. |
| `field_type` | One of `Text`, `Number`, `Select`, `Boolean`, `Date`, `Year`, `Textarea`, `MultiSelect`. |
| `unit` | Optional display unit such as `kg`; it does not define price units. |
| `help_text` | Optional bounded frontend guidance. |
| `options` | Canonical newline-delimited options for `Select`/`MultiSelect` only. |
| `is_active` | Inactive definitions are omitted from resolved category schemas. |

Client-supplied `attribute_key` is ignored on insert. Once persisted, changing the key is rejected. Public key identity therefore does not drift when labels change.

### `AOS Category Attribute Row`

Child relationship from a category to an attribute definition.

| Field | Purpose |
|---|---|
| `attribute` | Required Link to `AOS Ad Attribute`. One row per attribute per category is enforced by DB uniqueness. |
| `sort_order` | Category-specific order. Stable ID/label tie-breakers keep output deterministic. |
| `options_override` | Optional category-specific choices; valid only for `Select`/`MultiSelect`. |
| `is_required` | Whether a value is mandatory for Ads using the resolved schema. |
| `is_active` | Enables the relationship; an inactive child row can suppress an inherited attribute. |

A child leaf overrides the same root attribute definition. Duplicate rows are data corruption and fail closed; there is no “last row wins” compatibility behavior.

### Ads references

`AOS Ad.category` references the canonical category ID. `AOS Ad Attribute Value.attribute` references the canonical attribute definition ID and stores Ads-owned value/snapshot fields. Catalog checks those references before destructive category/attribute mutations. The Ads value DocType remains owned by Ads, not Catalog.

## Category hierarchy

The maximum hierarchy depth is two:

1. root group → sellable leaf; or
2. standalone root leaf.

A group must be root. A child parent must exist and be a root group. Self-parenting, cycles, child-of-leaf relationships and deeper chains are rejected. Public traversal is iterative/bounded and never recursively queries the database. Deleting a category with children is rejected. Deactivating a parent immediately makes its active children non-public without corrupting their stored relationship.

Frappe NestedSet is deliberately not used. The bounded link model avoids concurrent `lft`/`rgt` rewrites and makes the actual product invariant explicit.

## Attribute resolution and mutation safety

Public schema resolution walks root → leaf and applies leaf relationships last. Only active definitions/relationships are emitted. Options have one representation: newline-delimited canonical storage and ordered JSON arrays in public schemas. Category option overrides cannot convert a `Text` attribute into a select attribute; the definition type is authoritative.

Once an attribute has Ads values, changing its type or removing existing select choices is rejected with `ATTRIBUTE_IN_USE`. Adding choices is non-destructive and permitted. Deleting an attribute is rejected while any category relationship or Ads value references it; deactivation is the supported retirement path.

Once Ads depend on a category scope, destructive category-schema changes are restricted. A used leaf cannot move to another parent or change its pricing/service contract. Existing active relationships cannot be removed/deactivated, required flags cannot be changed, and category choice overrides cannot be narrowed. Optional attributes may be added and existing explicit choices may be expanded. Root-group attribute restrictions also account for Ads in child categories. Labels, ordering, images and deliberate category activation/deactivation remain independently mutable.

These restrictions are conservative by design. A materially different schema should use a new category/attribute identity instead of silently invalidating already-published Ads.

## Category image lifecycle

The Desk and server share the same Media lifecycle:

1. An authorized administrator saves the category, selects an image in `icon_preview`, and the Desk script calls `aos.api.v1.media.init_upload` with purpose `category_icon`.
2. The browser uploads bytes using the opaque Media upload contract. Catalog does not know the storage provider.
3. Desk calls `aos.api.v1.media.confirm_upload`; Media performs authoritative file/MIME/dimension validation.
4. Desk sets only `image_media` and saves the category.
5. During category validation, Catalog verifies that the Media object is valid for the `category_icon` purpose.
6. During the same category transaction, the new Media object is attached to the category first. Only after attachment succeeds is the previous Media object released through `MediaService`.
7. If any step fails, the category transaction rolls back. A newly confirmed but unattached object is client-cleaned when possible and remains covered by Media orphan reconciliation if that cleanup call is lost.
8. Removing `image_media` releases the old object through `MediaService`. Deleting a category does the same before the category transaction completes. Deactivation retains the valid attachment so reactivation does not create unmanaged media.

A stale prior Media ID is handled deliberately: if the old row is missing or is already attached elsewhere, Catalog logs a low-cardinality stale-reference event and does not mutate the unrelated Media resource. This allows the category reference to be repaired without making another resource unsafe.

Media's lifecycle may mark a released object `Replaced` or `Orphaned`; that is a managed Media cleanup state, not an unmanaged storage orphan.

Public category data exposes `image_url` only. Desk preview resolves `image_media` with `aos.api.v1.media.get_media_url`; raw storage keys, bucket names and credentials never cross the Catalog boundary.

## Frappe Desk behavior and authorization

Source-controlled DocPerm grants `AOS Category` and `AOS Ad Attribute` mutation to `System Manager`. Frappe enforces that permission server-side on normal Desk document writes; the HTML uploader's permission-aware buttons are only UX, not the security boundary. The `category_icon` Media purpose independently requires `AOS Category` write permission for upload/attachment.

There are no public Catalog mutation endpoints. Internal code using `ignore_permissions=True` is trusted server code and remains responsible for respecting the domain service/controller invariants; direct SQL/`frappe.db.set_value` mutations that bypass document hooks are not a supported Catalog administration path.

## Public API

Both public methods are GET-only and guest-readable. They use the standard AOS envelope:

```json
{"ok": true, "message": "...", "data": {}}
```

Failures use:

```json
{"ok": false, "message": "...", "error": "STABLE_CODE", "data": null}
```

Unknown request fields are rejected rather than silently ignored. Both endpoints are rate-limited before domain reads.

### `aos.api.v1.catalog.get_categories`

Request fields: none. Any supplied field is rejected with `INVALID_CATALOG_INPUT`.

Returns the complete active reference tree, bounded to 1,000 categories. There is intentionally no pagination because a client needs the small taxonomy reference set atomically; the hard bound prevents accidental unbounded growth. Ordering is `sort_order`, case-folded display name, then stable ID.

```json
[
  {
    "id": "Electronics",
    "name": "Electronics",
    "image_url": "https://public-media.example/...",
    "parent_id": null,
    "sort_order": 10,
    "is_group": 1,
    "children": [
      {
        "id": "Mobile Phones",
        "name": "Mobile Phones",
        "image_url": null,
        "parent_id": "Electronics",
        "sort_order": 20,
        "is_group": 0,
        "children": []
      }
    ]
  }
]
```

Only categories with a completely active, valid ancestry are returned. Missing parents are hidden. Corrupt cycles, illegal group parents, depth overflow or invalid stored sort values fail closed rather than exposing a partial taxonomy. Media IDs are not exposed.

Cold reads use one bounded category query plus one bulk Media attachment/URL query for all referenced images; it verifies purpose, `Attached` status, category name and `image_media` field, so a stale reference cannot project Media attached to another resource. There is no per-category Media N+1.

### `aos.api.v1.catalog.get_category_schema`

Required request field:

| Field | Required | Rules |
|---|---|---|
| `category` | yes | Scalar canonical category ID, max 140 characters. |

No aliases such as `category_id` are accepted.

Response:

```json
{
  "category": {
    "id": "Mobile Phones",
    "name": "Mobile Phones",
    "parent_id": "Electronics",
    "is_group": 0,
    "is_service": 0,
    "image_url": null
  },
  "attributes": [
    {
      "id": "Condition",
      "key": "condition",
      "label": "Condition",
      "type": "Select",
      "required": 1,
      "unit": "",
      "help_text": "",
      "options": ["New", "Used"],
      "sort_order": 10
    }
  ],
  "pricing": {
    "requirement": "Required",
    "allowed_price_types": ["Fixed", "Negotiable"],
    "allowed_units": []
  }
}
```

The response intentionally omits DocType field names that are not frontend contract, owners/internal users, storage IDs/keys and database metadata. Inactive categories or inactive ancestors are returned as `CATEGORY_NOT_FOUND` so private taxonomy state is not disclosed.

A cold schema read uses indexed identity lookups for only the requested category and at most its parent, followed by one category-attribute child query, one attribute-definition query and at most one bulk Media projection call. It never scans the full taxonomy to validate an Ads write or resolve one schema. The two-level chain and per-category relationship count are bounded.

## Stable error codes

| Error | HTTP | Meaning |
|---|---:|---|
| `INVALID_CATALOG_INPUT` | 422 | Missing, extra or invalid public request input. |
| `INVALID_CATEGORY` | 422 | Category identifier normalization failed. |
| `INVALID_CATEGORY_SCHEMA` | 422 | Administrative category/attribute configuration is invalid. |
| `INVALID_CATEGORY_TREE` | 422 | Parent/depth/group hierarchy is invalid. |
| `INVALID_CATEGORY_IMAGE` | 422 | Category Media ID is malformed. |
| `CATEGORY_NOT_SELLABLE` | 422 | Ads attempted to use a group category. |
| `CATEGORY_NOT_FOUND` | 404 | Category is missing or not public. |
| `CATEGORY_IN_USE` | 409 | Destructive category mutation would invalidate children/Ads. |
| `ATTRIBUTE_IN_USE` | 409 | Destructive attribute mutation would invalidate relationships/Ads. |
| `ATTRIBUTE_IDENTITY_IMMUTABLE` | 409 | Persisted `attribute_key` was changed. |
| `CATALOG_CONFLICT` | 409 | Catalog identity/concurrency contract conflict. |
| `CATALOG_DATA_ERROR` | 500 | Stored Catalog state is internally inconsistent. |
| `INTERNAL_ERROR` | 500 | Unexpected server failure. |

Human error text is public-safe; tracebacks, SQL, object keys, private URLs and credentials are logged server-side only.

## Caching and invalidation

Public category trees and category schemas are Redis-cached for 300 seconds under a schema-versioned `aos:catalog:v3:*` namespace. The cache stores only final public projections, including resolved public image URLs, so a hot read performs no Catalog or Media DB query.

Every supported category/attribute save/delete invalidates the tree and all schema keys immediately and registers the same invalidation with `frappe.db.after_commit`. The second invalidation closes the race where a reader repopulates old committed data while an admin transaction is still open. Redis failures are non-fatal; the database remains authoritative.

Catalog cache is not a permission store and contains no private Media data.

## Concurrency and transaction rules

Catalog admin traffic is lower than public reads, so correctness takes precedence over minimizing write locks.

- Existing category mutations acquire `SELECT ... FOR UPDATE` on the category first, then old/new parent rows, then every reusable attribute definition referenced by the previous/current category rows. New children lock the candidate parent before referenced attributes.
- Ads create/update schema validation locks the sellable leaf first, its parent second, the category relationship rows, and then all reusable attribute definitions in deterministic order. A concurrent category or attribute schema mutation therefore serializes with the listing write.
- Group demotion and child assignment serialize on the group row; a parent cannot become a leaf while a concurrent child assignment commits against stale state.
- Existing attribute mutations lock the attribute definition row before destructive-reference checks. Category saves also lock their referenced definitions, so relation validation cannot race an attribute deactivation/delete/type change.
- `uq_catalog_category_attribute` provides DB-level duplicate prevention; application validation is only the earlier friendly rejection.
- Category/attribute stable-name uniqueness and `attribute_key` uniqueness are DB-backed by DocType unique metadata.
- Category image replacement also goes through Media's attachment-target/media row locks. The category transaction updates `image_media`, attaches the new Media relationship and releases the old relationship atomically at the DB layer.
- Equal `sort_order` values are valid; stable name/ID tie-breakers make concurrent reorder results deterministic rather than corrupt.

Frappe's normal modified-timestamp conflict handling remains an additional lost-update guard for Desk document saves.

## Database indexes

`aos.patches.v1_0.install_catalog_indexes` owns manual indexes that DocType metadata cannot fully express:

| Index | Table | Columns | Purpose |
|---|---|---|---|
| `idx_catalog_order` | `AOS Category` | `sort_order, category_name, name` | Ordered full taxonomy reads. |
| `idx_catalog_parent_active_order` | `AOS Category` | `parent_aos_category, is_active, is_group, sort_order, name` | Parent/child lookup, active traversal and child integrity checks. |
| `idx_catalog_category_attribute_order` | `AOS Category Attribute Row` | `parenttype, parentfield, parent, sort_order, idx, name` | Ordered category-schema relation fetch. |
| `uq_catalog_category_attribute` | `AOS Category Attribute Row` | `parent, parenttype, parentfield, attribute` | One relationship per attribute/category at the database layer. |
| `idx_catalog_attribute_category_reference` | `AOS Category Attribute Row` | `attribute, parent, parenttype, parentfield` | Fast attribute-in-category reference checks for safe delete/retirement. |
| `idx_catalog_attribute_active_key` | `AOS Ad Attribute` | `is_active, attribute_key, name` | Active/key-oriented reference-data access. |
| `idx_catalog_ad_attribute_reference` | `AOS Ad Attribute Value` | `attribute, parent` | Fast destructive-attribute reference checks. |
| `idx_catalog_ad_category_reference` | `AOS Ad` | `category, name` | Fast category-in-use checks. |

The installer validates required tables/columns, verifies the exact named index definition, checks duplicate readiness before installing a unique constraint, and uses Frappe's `add_index`/`add_unique` APIs. It is listed both as the fresh-site post-model-sync patch and in `aos.migrate._SCHEMA_INVARIANT_INSTALLERS`; `after_migrate` reasserts it after every DocType synchronization so schema sync cannot silently remove a production-critical manual index.

DocType metadata additionally provides uniqueness for category `category_name`, attribute `label`, and immutable `attribute_key`.

## Scalability and operations

Catalog is reference data optimized for very high read fan-out:

- maximum 1,000 categories and depth two;
- maximum 500 category relationships per category and 1,500 select choices per definition/override;
- deterministic bounded queries only;
- one bulk Media URL projection rather than N+1 lookup;
- final-response Redis caching with immediate/post-commit invalidation;
- no recursive DB traversal or nested-set rewrite workload;
- public APIs return small reviewed fields only;
- server logs use low-cardinality Catalog events and never include storage credentials.

If the taxonomy grows beyond these product bounds, redesign the public contract/pagination/cache strategy deliberately rather than increasing limits without query-plan/load testing.

## Frontend usage, including Ads create/edit

A client should load `get_categories` for category selection and cache that public reference response locally as appropriate. When the user selects a sellable leaf, call `get_category_schema` and render controls from the ordered `attributes` array and `pricing` object. Submit attribute identity using the canonical `id` or `key` accepted by the hardened Ads validator; do not send labels as identity and do not duplicate type/options logic in the client or Ads.

For Ads create/edit specifically:

- choose only `is_group = 0` categories;
- use `type`, `required`, `options`, `unit` and `help_text` from the schema to render inputs;
- use `pricing.requirement`, `allowed_price_types` and `allowed_units` to render pricing UI;
- refresh the schema when the category changes;
- treat Catalog validation errors as authoritative if a schema was changed/deactivated since the form loaded.

The server still re-resolves and locks Catalog during Ads mutation; a frontend schema is never trusted as authorization or validation truth.

## Deployment and verification

For a new site, normal `bench migrate` synchronizes the DocTypes, runs `aos.patches.v1_0.install_catalog_indexes`, and then reasserts the same installer from `after_migrate`. No historical Catalog normalization/compatibility patch is required by the current source tree.

Production verification should include:

```text
bench --site <site> migrate
bench --site <site> run-tests --app aos
```

The Catalog-focused suite lives under `aos/api/catalog/tests`, with Media regression coverage in `aos/api/media/tests/test_category_integration.py` and controller metadata tests beside the Catalog DocTypes. Static repository checks should also run the API-documentation, repository/path, lint/format and source-contract validators used by CI.
