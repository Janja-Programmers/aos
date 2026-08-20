# Catalog taxonomy subsystem

The AOS Catalog domain is the marketplace taxonomy and listing-schema boundary. The repository does **not** define a separate Product, SKU, inventory, seller-catalog, variant, collection, or brand aggregate. Sellable marketplace inventory remains `AOS Ad`; Catalog supplies the category tree, category-specific attributes, and pricing rules that Ads consume.

## Domain model

| Entity | Purpose | Owner/capability |
| --- | --- | --- |
| `AOS Category` | Two-level public taxonomy, pricing policy, service-unit policy, and category icon reference | Frappe Role Permissions (baseline: System Manager) |
| `AOS Ad Attribute` | Reusable attribute definition and optional choice set | Frappe Role Permissions (baseline: System Manager) |
| `AOS Category Attribute Row` | Ordered category-to-attribute rule with active, required, and option-override controls | Child table of `AOS Category` |
| `AOS Ad Attribute Value` | Listing-owned values validated against the resolved category schema | Child table of `AOS Ad`; owned through the Ad |

Category names and attribute labels remain the current internal/public identifiers because that is the established repository contract. No email address or seller identity is exposed by Catalog endpoints.

## Boundaries

- `aos.api.v1.catalog` is the stable, guest-readable API boundary.
- `aos.api.catalog` owns rate limiting, response envelopes, public error mapping, and endpoint telemetry.
- `aos.services.catalog` owns normalization, validation, bounded reads, hierarchy checks, schema inheritance, privacy-safe serialization, and sellability decisions.
- DocType controllers invoke the same validators and invalidate compatibility caches after configuration changes.
- Ads call `CatalogService.get_sellable_category_chain()` before accepting category, pricing, or attribute values.
- Media remains the authority for category icon upload, ownership, confirmation, attachment, replacement, URL serialization, and release.

## Hierarchy and lifecycle

The established hierarchy is bounded to two levels:

1. A root group may have no parent.
2. A child must point to a root group and may not itself be a group.
3. A legacy standalone root leaf remains valid for backward compatibility with existing records and test fixtures.
4. Active children under an inactive parent are retained for staged administration but are hidden from all public reads and rejected for Ads.
5. Group categories are browseable but are never sellable.

Users with the relevant DocType Write permission may activate or deactivate categories and attributes through Desk. Public visibility requires the complete category ancestry to be active. Deleting a category uses the normal Frappe transaction, releases its attached category-icon Media relationship, and invalidates Catalog compatibility caches. Catalog does not introduce a separate moderation workflow; Ad moderation remains authoritative for listings.

## Attribute inheritance

Schemas resolve root-to-leaf. Later category rows are authoritative:

- an active child row can override required state, order, and choice options;
- a category-level choice override on a reusable `Text` attribute narrows that attribute to a single-choice `Select` for that category only;
- an inactive child row removes an inherited attribute;
- a child may explicitly re-enable an attribute disabled by its parent;
- duplicate rows are rejected on new writes;
- legacy duplicate rows are resolved deterministically by child-table order and row name until administrators clean them up.

Attribute definitions, category rows, and categories are loaded in bounded bulk queries. No serializer performs document loads or dynamic SQL.
Choice sets remain bounded, but category/master attribute choices use a dedicated limit large enough for legitimate taxonomies such as vehicle makes instead of the smaller generic Catalog choice limit.

## Pricing contract

`pricing_requirement` is one of `Required`, `Optional`, or `Hidden`. Allowed price types are allowlisted. Service units are accepted only for service categories. Hidden pricing clears type and unit choices. A required pricing schema must contain at least one allowed price type.

Numeric Ad prices and currency/localization behavior remain owned by Ads and Localization. Catalog does not perform currency conversion or relabel numeric amounts.

## Media contract

Category icons use the centralized `category_icon` Media purpose. It is public image media, limited by the Media policy, and attachable to one `AOS Category` by a user with Write permission on that category. New icon changes require a confirmed Media ID; the legacy URL field is a compatibility cache, not storage authority. Replacement and deletion reuse `MediaService`; Catalog has no storage or presigned-upload implementation.

### Desk category-image uploader

The `AOS Category` Desk form provides the supported administrative workflow:

1. Save the category so the attachment target has a durable identity.
2. Select **Upload image** or **Replace image** in the Category Image section.
3. The browser validates the basic image type, size, and dimensions, calculates SHA-256 when Web Crypto is available, and calls `aos.api.v1.media.init_upload`.
4. The file is uploaded directly to the returned presigned URL with the returned headers.
5. Desk calls `aos.api.v1.media.confirm_upload`, assigns the confirmed Media ID, and saves the category.
6. The category controller attaches the new Media relationship and safely releases the previous one in the request transaction.

Only users with effective Write permission on `AOS Category` see enabled image actions. The server-side Media policy and category controller remain authoritative for Frappe permissions, ownership, purpose, MIME, extension, size, dimensions, checksum, lifecycle, and attachment validation. The raw `icon` cache is hidden and read-only, and `icon_media` is read-only so administrators do not bypass the supported workflow. Failed browser uploads attempt bounded cleanup through `delete_media`; centralized orphan cleanup remains the fallback for ambiguous network outcomes. **Remove image** clears the relationship through a normal category save so the shared Media lifecycle handles release.

## Search, moderation, outbox, and notifications

The repository has no independent Catalog index, moderation job, outbox event, or user notification. Those integrations operate on Ads. Catalog hardening therefore does not create speculative companion workflows. Deactivating or deleting a category prevents new or updated Ads from using it; existing Ad discovery remains governed by the existing Ad lifecycle and search/removal mechanisms.

## Security and failure behavior

- Public endpoints expose only reviewed taxonomy/schema fields.
- Catalog mutation is administrative Desk behavior; there are no seller-facing Catalog mutation APIs.
- Seller-controlled Ads cannot provide owner, approval, ranking, counters, or arbitrary schema fields through Catalog.
- Invalid hierarchy/schema input is rejected with stable 422 identifiers.
- Missing or inactive public categories use a generic 404 response.
- Corrupt server-side Catalog data fails closed with a generic 500 response and a redacted Error Log entry.
- Reads are rate-limited by IP before database work.

## Operations

Low-cardinality counters are emitted as `aos_catalog_events_total` for read/configuration event and outcome classes. IDs, labels, descriptions, search terms, emails, and media URLs are never metric labels. Configuration rejections and internal data failures are structured-log events; successful public reads are metrics-only to avoid noisy logs.

The additive Catalog patches install bounded-query indexes and normalize only safe defaults. Catalog Desk access is intentionally resolved at runtime through Frappe DocPerm / Custom DocPerm / Role Permissions Manager; the legacy permission patch no longer deletes administrator-configured overrides. See [migration.md](migration.md), [testing.md](testing.md), and the public [API contract](api.md).
