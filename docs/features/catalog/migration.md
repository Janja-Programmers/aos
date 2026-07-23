# Catalog migration

Patch: `aos.patches.v1_0.harden_catalog_subsystem`

## Additive indexes

- `idx_aos_category_public_tree` on active/parent/group/sort predicates.
- `idx_aos_category_attribute_lookup` on parent/parentfield/active/sort predicates.
- `idx_aos_attribute_active_label` on active/label predicates.

The patch checks table, column, and index existence before using Frappe's `add_index`. It is safe on partially upgraded sites and repeatable.

## Data normalization

The patch performs only deterministic, non-destructive defaults:

- blank category pricing requirements become `Optional`;
- null or negative category and category-attribute sort orders become `0`.

It does not delete categories, merge duplicate legacy rows, rename identifiers, rewrite Media, or force inactive configuration active. New duplicate attribute rows are rejected by validation. Legacy duplicates remain readable with deterministic last-row authority so production data can be reviewed manually.

## Deployment sequence

1. Take and verify the normal encrypted backup.
2. Deploy the cumulative app source.
3. Run `bench --site <site> migrate`.
4. Run focused Catalog database tests.
5. Inspect Catalog configuration-rejection logs and `aos_catalog_events_total`.
6. Run the full app suite and repository validators before promotion.

The patch contains no explicit commit. Bench/Frappe migration transaction ownership remains authoritative.
