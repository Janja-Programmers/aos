# Catalog

## Overview
Catalog owns AOS marketplace taxonomy: categories, reusable attribute definitions, category-specific attribute rules/dependencies, pricing schema, and category media relationship.

## Responsibilities
Catalog defines sellable categories, hierarchy, attribute keys/types/options, required/order overrides, dependency mappings, service/pricing semantics, Desk administration rules and canonical category schema serialization/validation.

## Boundaries
Media owns category image lifecycle. Ads stores listing values/snapshots but validates categories and attributes through Catalog; Ads does not define or mutate Catalog schema.

## Architecture
```text
Versioned Catalog API / Desk -> CatalogService + controllers -> category/attribute DocTypes -> MariaDB + shared cache
                                                   -> Media boundary for category images
Ads validation -> CatalogService resolved schema/locks
```

## Data Model
- `AOS Category`: stable category ID, hierarchy/sellability/pricing rules and Media link.
- `AOS Ad Attribute`: reusable stable attribute definition with immutable public `attribute_key`.
- `AOS Category Attribute Row`: category-to-attribute activation/requirement/order/option overrides.
- `AOS Category Attribute Dependency Row`: category-scoped parent-option to child-option dependency rules.
- `AOS Ad Attribute Value`: Ads-owned listing values validated against Catalog definitions.

## Fields
| Model | Field | Required / constraint | Purpose |
|---|---|---|---|
| AOS Category | `category_name` / stable name | unique | Human label and stable category identity. |
| AOS Category | parent/group/active/order | constrained | Hierarchy and public availability/order. |
| AOS Category | service/pricing fields | canonical enum/list | Ads pricing schema. |
| AOS Category | `image_media` | Media Link | Canonical category image. |
| AOS Ad Attribute | `attribute_key` | immutable unique | Stable client/schema identifier. |
| AOS Ad Attribute | type/options/unit/active | validated | Reusable attribute definition. |
| Category child rows | attribute/required/order/options/dependencies | scoped constraints | Category-specific resolved schema. |

## API
Catalog endpoints expose active category trees, resolved category schemas and authorized administration/media operations from the generated inventory. Public clients consume these endpoints; they do not inspect DocTypes or reconstruct dependency rules.

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_attribute_options` | GET | Guest allowed | Client |
| `get_categories` | GET | Guest allowed | Client |
| `get_category_schema` | GET | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Cross-feature Dependencies
Catalog consumes Media for category images. Ads consumes Catalog service validation, locks and serializers for create/update/filter/schema behavior. Other domains should reference stable category/attribute identities rather than replicate Catalog rules.

## Transaction / Concurrency Model
Schema mutations validate relationships and take the necessary Catalog row locks before changing dependent structures. Unique constraints protect category/attribute identities. Cache invalidation follows authoritative writes and transactions rely on normal Frappe semantics.

## Caching
Resolved category/schema reads use shared cache with explicit invalidation on Catalog changes. Cache entries are derived and never the authoritative schema.

## Performance / Scalability
Read-heavy category/schema paths benefit from shared caching and targeted indexes. Administration writes are lower frequency but still validate/lock narrowly. Ads obtains resolved Catalog rules without rewriting entire child tables unnecessarily. Production read/write mix needs load testing.

## Testing
Tests under `aos/api/catalog/tests` plus Ads integration suites cover hierarchy, schema resolution, dependencies, image Media integration, authorization, cache invalidation, indexes and concurrency. Factories create valid related categories/attributes and tracked teardown prevents Catalog pollution across suites.

## Detailed Reference

### Public transport boundary

Every Catalog v1 wrapper delegates through the platform canonical `aos.api.shared.transport.execute_endpoint` boundary before Catalog request validation. Frappe's framework-owned `cmd` routing field is removed there; all genuine client fields remain visible so unknown fields continue to fail with the stable Catalog validation contract. Catalog uses the shared transport boundary directly.


Catalog is the canonical marketplace taxonomy and listing-schema domain. It owns category identity and hierarchy, reusable attribute definitions, category-specific attribute configuration, and the pricing/schema metadata that Ads consumes. Catalog does not own listing attribute values, user-generated media, object-storage configuration, or provider credentials.

The production design is intentionally small: categories form a maximum two-level root-group → leaf hierarchy (with a root leaf also allowed), attributes are reusable definitions, and `AOS Category Attribute Row` applies an attribute to a category. This bounded model replaces the need for nested-set bookkeeping and makes hierarchy validation, locking, traversal, and indexing deterministic.

### Responsibilities and boundaries

Catalog is the single source of truth for:

- stable category IDs, display names, parent relationships, activation and ordering;
- whether a category is a grouping node or sellable leaf;
- service/pricing rules used by Ads validation;
- reusable attribute identity, type, label, unit, help text and canonical options;
- category-specific attribute activation, requirement, order and select-option overrides;
- dependent-attribute relationships and parent-option → child-option mappings;
- the authoritative category image relationship through Media.

Ads stores listing values and immutable display/type/unit snapshots for stable listing rendering, but Ads does not define the category or attribute schema. Ads create/update validation calls `CatalogService` and takes Catalog row locks before using a sellable schema. Public clients must use the Catalog endpoints rather than inspect Frappe DocTypes.

Catalog does **not** currently model numeric min/max constraints, arbitrary string-length constraints, or search/filter flags on attributes. Frontends must not invent those fields. If those capabilities are added later, their definitions belong here in Catalog rather than Ads.

### Dependency on Media

Category images use the existing canonical Media purpose `category_icon`. Its policy is defined only by Media and currently permits public JPEG, PNG and WebP images up to 5 MiB with dimensions from 16×16 through 4096×4096. Media remains authoritative for MIME/magic-byte validation, dimensions, visibility, object identity, storage location, upload confirmation, attachment limits and cleanup.

Catalog contains no MinIO/S3 bucket, object-key, credential, `File`, Attach-field or direct provider implementation. The category stores only `image_media`, a Link to `AOS Media Object`. It never persists the resolved URL as category truth.

`owner_user` on the Media object records the administrator who initialized the upload for audit/manage purposes. Resource authority is the Media attachment to the `AOS Category`, and the `category_icon` purpose requires `AOS Category` write permission. Replacement/removal can therefore safely release an old administrator-uploaded object through the trusted resource lifecycle without treating category content as ordinary seller-owned media.

### Data model

#### `AOS Category`

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
| `allowed_price_types` | Canonical newline-delimited allowlist from `Fixed`, `Negotiable`, `Contact for price`. |
| `allowed_price_units` | Canonical newline-delimited service-unit choices. Empty for non-service categories. |
| `attributes` | Child table of category-specific attribute relationships. |
| `attribute_dependencies` | Child table of category-scoped dependency groups: one row per dependent attribute + parent option, with allowed child options stored one per line. |
| `image_media` | Hidden/read-only canonical `AOS Media Object` link for the category image. |
| `icon_preview` | Desk-only HTML control. It does not store image authority. |

Layout fields such as Section Break/Column Break have no domain semantics. Category hierarchy is represented by the current parent/group fields; nested-set fields are not part of this model.

#### `AOS Ad Attribute`

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

#### `AOS Category Attribute Row`

Child relationship from a category to an attribute definition.

| Field | Purpose |
|---|---|
| `attribute` | Required Link to `AOS Ad Attribute`. One row per attribute per category is enforced by DB uniqueness. |
| `sort_order` | Category-specific order. Stable ID/label tie-breakers keep output deterministic. |
| `options_override` | Category-specific choices for an independent `Select`/`MultiSelect`. It must be blank when `depends_on_attribute` is set. |
| `depends_on_attribute` | Optional direct parent attribute for dependent `Select` fields. The parent must be an active `Select` relationship in the same category configuration. When set, the dependent attribute's effective options are derived solely from `attribute_dependencies`. |
| `is_required` | Whether a value is mandatory for Ads using the resolved schema. |
| `is_active` | Enables the relationship; an inactive child row can suppress an inherited attribute. |

A child leaf overrides the same root attribute definition. Duplicate rows are data corruption and fail closed; there is no “last row wins” duplicate-row behavior.


#### `AOS Category Attribute Dependency Row`

Grouped dependency mapping stored under `AOS Category.attribute_dependencies`. For a dependent `Select`, these rows are the category-level source of truth for its effective options: the option universe is the stable union of all `child_options` rows for that dependent attribute. Persistence is optimized for administrator entry: one row represents one parent option and all child options allowed for that parent.

| Field | Purpose |
|---|---|
| `child_attribute` | The dependent attribute relation, for example `Model`. That relation must declare `depends_on_attribute`. |
| `parent_option` | One effective option of the direct parent attribute, for example `HP`. Only one row is allowed for each category + child attribute + parent option. |
| `child_options` | Required `Small Text` containing the effective dependent options allowed for this parent option, one canonical option per line. For example `EliteBook\nProBook`. |
| `mapping_key` | Hidden deterministic SHA-256 row identity derived from category + child attribute + parent option. Used by the database unique index without an oversized utf8mb4 composite key. |

The resolver expands `child_options` into logical parent/child edges in memory and derives the dependent attribute's ordered effective option list from those mappings. `options_override` is deliberately unavailable for dependent rows, so administrators never enter the same option twice and there is no competing option source. Global definition options are also ignored for that category relationship once it is dependent. A child option may map to more than one parent option when the domain genuinely requires it by appearing in multiple parent rows. Duplicate parent groups are rejected. If the dependent attribute is required, every effective parent option must have at least one child option; this prevents a parent selection that makes the form impossible to complete. The category remains bounded to 10,000 expanded logical dependency edges. Ordinary definition/override option lists and each individual parent mapping remain bounded to 1,500 choices, while a dependent attribute may derive up to 10,000 unique effective options across its mappings.

#### Ads references

`AOS Ad.category` references the canonical category ID. `AOS Ad Attribute Value.attribute` references the canonical attribute definition ID and stores Ads-owned value/snapshot fields. Catalog checks those references before destructive category/attribute mutations. The Ads value DocType remains owned by Ads, not Catalog.

### Category hierarchy

The maximum hierarchy depth is two:

1. root group → sellable leaf; or
2. standalone root leaf.

A group must be root. A child parent must exist and be a root group. Self-parenting, cycles, child-of-leaf relationships and deeper chains are rejected. Public traversal is iterative/bounded and never recursively queries the database. Deleting a category with children is rejected. Deactivating a parent immediately makes its active children non-public without corrupting their stored relationship.

Frappe NestedSet is deliberately not used. The bounded link model avoids concurrent `lft`/`rgt` rewrites and makes the actual product invariant explicit.

### Attribute resolution and mutation safety

Public schema resolution walks root → leaf and applies leaf relationships last. Only active definitions/relationships are emitted. Independent Select/MultiSelect relationships use `options_override` when present, otherwise the global attribute definition options. Dependent Select relationships ignore both of those option lists for that category and derive their effective options from the union of their dependency mappings. Options are emitted as deterministic ordered JSON arrays in public schemas. Category option overrides cannot convert a `Text` attribute into a select attribute; the definition type is authoritative.

Dependent attributes are intentionally constrained rather than implemented as arbitrary expressions. Both the child and its direct parent must be active `Select` relationships in the same category configuration, each child has at most one direct parent, and dependency graphs must be acyclic. Multi-level chains such as `Brand → Model → Variant` are supported. A required child requires a required parent. The resolved attribute array is topologically ordered, so dependency parents always precede their children even when configured sort orders conflict.

For a dependent relationship, every effective child option must have at least one mapping to an effective parent option. Persistence groups those logical mappings by parent option, but resolution expands them before applying validation. The mapping is category-scoped, so different categories can reuse the same attribute definition with different dependency rules. A leaf relationship override is final authority for that attribute, including whether it is dependent and which grouped mapping rows from that category apply.

Once an attribute has Ads values, changing its type or removing existing select choices is rejected with `ATTRIBUTE_IN_USE`. Attributes participating anywhere in dependency rules also cannot be deactivated or retyped in place. Global options of an attribute used as a dependency parent cannot change in place because category mappings may reference those parent values; a dependency child's global options do not define its dependent-category choices. Independent category choices evolve through `options_override`; dependent category choices evolve through `attribute_dependencies` only. Deleting an attribute is rejected while any category relationship or Ads value references it; deactivation is the supported retirement path when no dependency contract blocks it.

Once Ads depend on a category scope, destructive category-schema changes are restricted. A used leaf cannot move to another parent or change its pricing/service contract. Existing active relationships cannot be removed/deactivated, required flags and dependency parents cannot be changed, category choices cannot be narrowed, and existing logical dependency mapping pairs cannot be removed or reassigned. Safe additive evolution remains supported: optional attributes, new options and new logical dependency pairs may be added by extending an existing parent row or adding a new parent row while preserving every already-valid value/combination. A category that inherited global options may move to a category override only when that override is a superset of the current canonical choices. Root-group restrictions also account for Ads in child categories. Labels, ordering, images and deliberate category activation/deactivation remain independently mutable.

These restrictions are conservative by design. A materially different schema should use a new category/attribute identity instead of silently invalidating already-published Ads.

### Category image lifecycle

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

### Frappe Desk behavior and authorization

Source-controlled DocPerm provides the default administrative mutation grant for `AOS Category` and `AOS Ad Attribute`. Effective authorization is intentionally evaluated through Frappe's permission engine, so Role Permissions Manager / Custom DocPerm may grant the same capabilities to additional operational roles without application-code role checks. Normal Desk writes are enforced server-side; the HTML uploader reads the form's effective level-zero Write permission only for UX. The `category_icon` Media purpose independently requires effective `AOS Category` write permission for upload/attachment.

There are no public Catalog mutation endpoints. Internal code using `ignore_permissions=True` is trusted server code and remains responsible for respecting the domain service/controller invariants; direct SQL/`frappe.db.set_value` mutations that bypass document hooks are not a supported Catalog administration path.

### Public API

All three public methods are GET-only and guest-readable. They use the standard AOS envelope:

```json
{"ok": true, "message": "...", "data": {}}
```

Failures use:

```json
{"ok": false, "message": "...", "error": "STABLE_CODE", "data": null}
```

Unknown request fields are rejected rather than silently ignored. All endpoints are rate-limited before domain reads.

#### `aos.api.v1.catalog.get_categories`

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

#### `aos.api.v1.catalog.get_category_schema`

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
      "id": "Brand",
      "key": "brand",
      "label": "Brand",
      "type": "Select",
      "required": 1,
      "unit": "",
      "help_text": "",
      "options": ["HP", "Apple"],
      "sort_order": 10
    },
    {
      "id": "Model",
      "key": "model",
      "label": "Model",
      "type": "Select",
      "required": 1,
      "unit": "",
      "help_text": "",
      "options": ["EliteBook", "ProBook", "MacBook Air"],
      "sort_order": 20,
      "depends_on": {"id": "Brand", "key": "brand"}
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

A cold schema read uses indexed identity lookups for only the requested category and at most its parent, followed by one category-attribute child query, one bounded grouped dependency-row query, one attribute-definition query and at most one bulk Media projection call. It never scans the full taxonomy to validate an Ads write or resolve one schema. The two-level chain and per-category relationship count are bounded.


#### `aos.api.v1.catalog.get_attribute_options`

Returns the selectable options for one resolved category attribute. For an independent `Select`/`MultiSelect`, omit `parent_value` and the endpoint returns all effective options. For a dependent attribute, `parent_value` is required and only child options mapped to that exact canonical parent option are returned.

| Field | Required | Rules |
|---|---|---|
| `category` | yes | Scalar canonical category ID, max 140 characters. |
| `attribute` | yes | Canonical attribute `id` or immutable `key`, max 140 characters. |
| `parent_value` | dependent attributes only | Exact canonical parent option, max 120 characters. Must exist in the resolved parent attribute options. |

Example for `Brand = HP`:

```json
{
  "category_id": "Laptops",
  "attribute": {
    "id": "Model",
    "key": "model",
    "label": "Model"
  },
  "depends_on": {
    "attribute_id": "Brand",
    "attribute_key": "brand",
    "value": "HP"
  },
  "options": ["EliteBook", "ProBook"]
}
```

For the same attribute with `parent_value=Apple`, the response may be `options: ["MacBook Air"]`. A valid parent option may return an empty child list only when the dependent attribute is optional; required dependencies are validated so every parent choice has at least one valid child. An invalid/missing parent value for a dependent attribute fails with `INVALID_CATALOG_INPUT`; the endpoint never guesses labels or falls back to all models.

The dependency grouping table itself is not exposed. This keeps persistence details private and avoids sending potentially large brand/model matrices in every category-schema response. Results are cached by category + attribute reference + canonical parent value.

### Stable error codes

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

### Caching and invalidation

Public category trees, category schemas and dependent-option responses are Redis-cached for 300 seconds under a schema-versioned `aos:catalog:v5:*` namespace. Catalog also keeps one bounded internal resolved-attribute cache per category containing dependency maps but no private Media/storage data. This prevents a category with many brands/models from reloading the entire grouped dependency table for every distinct parent-option cache miss. Public responses never expose that internal matrix, and hot reads perform no Catalog or Media DB query.

Every supported category/attribute save/delete invalidates the tree, all schema keys, all dependent-option keys and all internal resolved-attribute keys immediately and registers the same invalidation with `frappe.db.after_commit`. The second invalidation closes the race where a reader repopulates old committed data while an admin transaction is still open. Redis failures are non-fatal; the database remains authoritative.

Catalog cache is not a permission store and contains no private Media data.

### Concurrency and transaction rules

Catalog admin traffic is lower than public reads, so correctness takes precedence over minimizing write locks.

- Existing category mutations acquire `SELECT ... FOR UPDATE` on the category first, then old/new parent rows, then every reusable attribute definition referenced by the previous/current category rows. New children lock the candidate parent before referenced attributes.
- Ads create/update schema validation locks the sellable leaf first, its parent second, the category relationship rows, the grouped dependency rows, and then all reusable attribute definitions in deterministic order. A concurrent category or attribute schema mutation therefore serializes with the listing write.
- Group demotion and child assignment serialize on the group row; a parent cannot become a leaf while a concurrent child assignment commits against stale state.
- Existing attribute mutations lock the attribute definition row before destructive-reference checks. Category saves also lock their referenced definitions, so relation validation cannot race an attribute deactivation/delete/type change.
- `uq_catalog_category_attribute` provides DB-level category↔attribute duplicate prevention. `uq_catalog_dependency_mapping` provides DB-level uniqueness for each category + dependent attribute + parent-option group through the fixed-width SHA-256 `mapping_key`; application validation remains the earlier friendly rejection.
- Category/attribute stable-name uniqueness and `attribute_key` uniqueness are DB-backed by DocType unique metadata.
- Category image replacement also goes through Media's attachment-target/media row locks. The category transaction updates `image_media`, attaches the new Media relationship and releases the old relationship atomically at the DB layer.
- Equal `sort_order` values are valid; stable name/ID tie-breakers make concurrent reorder results deterministic rather than corrupt.

Frappe's normal modified-timestamp conflict handling remains an additional lost-update guard for Desk document saves.

### Database indexes

`aos.patches.v1_0.install_catalog_indexes` owns manual indexes that DocType metadata cannot fully express:

| Index | Table | Columns | Purpose |
|---|---|---|---|
| `idx_catalog_order` | `AOS Category` | `sort_order, category_name, name` | Ordered full taxonomy reads. |
| `idx_catalog_parent_active_order` | `AOS Category` | `parent_aos_category, is_active, is_group, sort_order, name` | Parent/child lookup, active traversal and child integrity checks. |
| `idx_catalog_category_attribute_order` | `AOS Category Attribute Row` | `parenttype, parentfield, parent, sort_order, idx, name` | Ordered category-schema relation fetch. |
| `uq_catalog_category_attribute` | `AOS Category Attribute Row` | `parent, parenttype, parentfield, attribute` | One relationship per attribute/category at the database layer. |
| `idx_catalog_attribute_category_reference` | `AOS Category Attribute Row` | `attribute, parent, parenttype, parentfield` | Fast attribute-in-category reference checks for safe delete/retirement. |
| `idx_catalog_dependency_parent_reference` | `AOS Category Attribute Row` | `depends_on_attribute, parent, parenttype, parentfield` | Fast detection of attributes used as dependency parents. |
| `idx_catalog_dependency_mapping_order` | `AOS Category Attribute Dependency Row` | `parenttype, parentfield, parent, child_attribute, idx, name` | Bounded deterministic grouped dependency fetch for one category chain. |
| `uq_catalog_dependency_mapping` | `AOS Category Attribute Dependency Row` | `mapping_key` | One grouped row per category + dependent attribute + parent option, using a fixed-width hash key. |
| `idx_catalog_dependency_child_reference` | `AOS Category Attribute Dependency Row` | `child_attribute, parent, parenttype, parentfield` | Fast detection of attributes used as dependent children. |
| `idx_catalog_attribute_active_key` | `AOS Ad Attribute` | `is_active, attribute_key, name` | Active/key-oriented reference-data access. |
| `idx_catalog_ad_attribute_reference` | `AOS Ad Attribute Value` | `attribute, parent` | Fast destructive-attribute reference checks. |
| `idx_catalog_ad_category_reference` | `AOS Ad` | `category, name` | Fast category-in-use checks. |

The installer validates required tables/columns, verifies the exact named index definition, checks duplicate readiness before installing a unique constraint, and uses Frappe's `add_index`/`add_unique` APIs. It is listed both as the fresh-site post-model-sync patch and in `aos.migrate._SCHEMA_INVARIANT_INSTALLERS`; `after_migrate` reasserts it after every DocType synchronization so schema sync cannot silently remove a production-critical manual index.

DocType metadata additionally provides uniqueness for category `category_name`, attribute `label`, and immutable `attribute_key`.

### Scalability and operations

Catalog is reference data optimized for very high read fan-out:

- maximum 1,000 categories and depth two;
- maximum 500 category relationships per category, 1,500 select choices per definition/override or individual dependency row, 10,000 unique effective options per dependent attribute, and 10,000 expanded dependency mappings per category;
- deterministic bounded queries only;
- one bulk Media URL projection rather than N+1 lookup;
- final-response Redis caching with immediate/post-commit invalidation, including category+attribute+parent-value option lookups;
- no recursive DB traversal or nested-set rewrite workload;
- public APIs return small reviewed fields only;
- server logs use low-cardinality Catalog events and never include storage credentials.

If the taxonomy grows beyond these product bounds, redesign the public contract/pagination/cache strategy deliberately rather than increasing limits without query-plan/load testing.

### Frontend usage, including Ads create/edit

A client should load `get_categories` for category selection and cache that public reference response locally as appropriate. When the user selects a sellable leaf, call `get_category_schema` and render controls from the ordered `attributes` array and `pricing` object. Submit attribute identity using the canonical `id` or `key` accepted by the canonical Ads validator; do not send labels as identity and do not duplicate type/options logic in the client or Ads.


Dependent-attribute frontend flow:

1. Render attributes in the schema order. Catalog guarantees a dependency parent appears before its child.
2. When an attribute has `depends_on`, keep the child disabled/empty until its parent has a value.
3. Call `get_attribute_options(category, attribute, parent_value)` when the parent selection changes. For example, `brand=HP` may return `EliteBook`/`ProBook`, while `brand=Apple` returns `MacBook Air`.
4. Clear any selected child value whenever the parent changes, then render only the returned options. Repeat the same process for deeper chains such as Model → Variant.
5. Do not filter using hardcoded brand/model maps in the client. The Ads mutation validator re-resolves and locks the same Catalog dependency rules and rejects mismatched combinations.

For Ads create/edit specifically:

- choose only `is_group = 0` categories;
- use `type`, `required`, `options`, `unit`, `help_text` and optional `depends_on` from the schema to render inputs;
- use `pricing.requirement`, `allowed_price_types` and `allowed_units` to render pricing UI;
- refresh the schema when the category changes;
- treat Catalog validation errors as authoritative if a schema was changed/deactivated since the form loaded.

The server still re-resolves and locks Catalog during Ads mutation; a frontend schema is never trusted as authorization or validation truth.

### Deployment and verification

For a new site, normal `bench migrate` synchronizes the DocTypes, runs `aos.patches.v1_0.install_catalog_indexes`, and reasserts the same installer from `after_migrate`.

Production verification should include:

```text
bench --site <site> migrate
bench --site <site> run-tests --app aos
```

The Catalog-focused suite lives under `aos/api/catalog/tests`, with Media regression coverage in `aos/api/media/tests/test_category_integration.py` and controller metadata tests beside the Catalog DocTypes. Static repository checks should also run the API-documentation, repository/path, lint/format and source-contract validators used by CI.

## Security
Only authorized Desk operations may mutate the taxonomy or category attributes. Seller/Ad writes resolve the current Catalog definitions through the owning validation service; public category endpoints project bounded metadata and never grant mutation rights or accept owner/role values from client payloads.
