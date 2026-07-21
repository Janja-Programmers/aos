# Localization production operations

## Scope

The localization feature provides:

- Validated country, currency, and language master data.
- Validated system defaults from `AOS Settings`.
- Guest/request locale resolution.
- Authenticated stored preference resolution.
- Country-scoped, searchable, paginated active locations.

It does not authorize access to private data. Geo-IP and language headers are hints used only for guest personalization.

## Required configuration

`AOS Settings` must reference valid master records for:

- `default_country`
- `default_currency`
- `default_language`

Default currency and language must be enabled when the underlying Frappe DocType exposes an `enabled` field. Invalid defaults cause public localization bootstrap to fail closed with `CONFIG_ERROR`.

## Cache model

Two localization cache entries are maintained:

- Validated defaults.
- Complete locale bundle.

Both expire after five minutes and are immediately invalidated by changes to:

- `Country`
- `Currency`
- `Language`
- `AOS Settings`

User preferences use a separate five-minute per-user cache. Partial writes acquire a row lock to avoid lost updates under concurrent requests. `AOS User Preference` insert, update, and delete events invalidate it.

Cache failure is non-fatal: requests fall back to canonical database reads.

## Database contract

Required indexes:

- Unique `(country, location)` for `AOS Location`.
- `(country, is_active, sort_order, location)` for paginated location reads.
- Unique `user` for `AOS User Preference`.

Run migrations before releasing the feature:

```bash
bench --site <site> migrate
```

The index patch is idempotent:

```text
aos.patches.v1_0.harden_localization_indexes
```

## Smoke test

1. Fetch `get_locale_bundle`; confirm `schema_version`, non-empty enabled lists, and configured defaults.
2. Resolve guest context with no parameters; confirm default or header sources.
3. Resolve with explicit country, currency, and language; confirm all sources are `request`.
4. Authenticate and resolve context with conflicting inputs; confirm stored preferences remain authoritative.
5. Fetch locations with `limit=2`; follow `next_offset` and confirm stable, non-overlapping pages.
6. Verify inactive locations are absent.
7. Submit invalid `limit`, `offset`, and overlong search values; confirm 422 responses with stable errors.
8. Update an account currency; confirm country/language are unchanged and `/me` reflects the new value.
9. Create seller ad activity and verify a country change returns `MARKET_LOCKED` while language/currency remain editable.

## Monitoring

Use existing generic request metrics and rate-limit metrics for these endpoints. Alert investigation should distinguish:

- `CONFIG_ERROR`: invalid production defaults or master-data state.
- `INTERNAL_ERROR`: unexpected application/database failure.
- `RATE_LIMIT`: expected abuse protection.
- Validation errors: client integration defects or malformed traffic.

Repeated `CONFIG_ERROR` is a release blocker because new sessions and guest bootstrap cannot resolve safely.

## Rollback

The API additions are backward compatible:

- Existing `resolve_preference_context` remains available.
- Existing top-level bundle keys remain available.
- Existing location keys remain available; pagination metadata is additive.

Rolling back application code after the index patch is safe because the added index is non-destructive. Do not drop it during an emergency rollback.
