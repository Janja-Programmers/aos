# Localization API v1

AOS treats country, currency, and language as independent values. Country scopes marketplace content and valid ad locations, currency controls posting/display conversion, and language controls localized UI/content. One preference never silently changes another.

## Ownership boundary

Localization owns public master data and effective request-context resolution. Persisted authenticated-user settings remain account resources under `aos.api.v1.accounts`.

- Localization: countries, currencies, languages, locations, defaults, guest/request context.
- Accounts: read and update the current user's saved country, currency, and language.
- Auth: initialize or repair the same saved preference during registration, login, and `/me` bootstrap.

## Locale bundle

`GET /api/method/aos.api.v1.localization.get_locale_bundle`

Returns every Frappe `Country`, only enabled Frappe `Currency` and `Language` rows, and validated defaults from `AOS Settings`.

The endpoint uses a bounded five-minute server cache and bulk master-data queries. Cache entries are invalidated when `Country`, `Currency`, `Language`, or `AOS Settings` changes.

Response data:

```json
{
  "schema_version": "1.1",
  "cache_ttl_seconds": 300,
  "countries": [
    {"id": "Kenya", "name": "Kenya", "code": "KE", "flag": "🇰🇪"}
  ],
  "currencies": [
    {
      "id": "USD",
      "code": "USD",
      "symbol": "$",
      "name": "US Dollar",
      "enabled": true,
      "is_default": true
    }
  ],
  "languages": [
    {
      "id": "en",
      "code": "en",
      "name": "English",
      "flag": null,
      "enabled": true,
      "is_default": true
    }
  ],
  "defaults": {
    "country": "Kenya",
    "currency": "USD",
    "language": "en"
  }
}
```

Invalid, disabled, or missing defaults fail closed with `CONFIG_ERROR`; an invalid configuration is never cached as a valid bundle.

## Resolve locale context

Preferred endpoint:

`GET|POST /api/method/aos.api.v1.localization.resolve_locale_context`

Compatibility endpoint:

`GET|POST /api/method/aos.api.v1.localization.resolve_preference_context`

Both call the same implementation.

Authenticated requests always use the stored `AOS User Preference`. Request parameters cannot override authenticated market state. Guest values resolve independently in this order:

1. Explicit request value.
2. `CF-IPCountry`, then `X-Country-Code`, for a missing country.
3. `Accept-Language`, for a missing language.
4. Validated `AOS Settings` defaults.

Geo-IP headers are personalization hints only. They never authorize private data access.

`Accept-Language` processing is bounded to 512 characters and 20 entries, validates quality values in the RFC range `0..1`, canonicalizes tags, and tries exact then primary tags (`en-US`, then `en`). Unknown or disabled inferred languages are skipped. Invalid explicit inputs are rejected.

Response data contains rich `country`, `currency`, and `language` objects plus per-field `sources`:

- `request`
- `geoip`
- `accept_language`
- `default`
- `user_preference`

## Locations

`GET|POST /api/method/aos.api.v1.localization.get_locations`

Parameters:

| Parameter | Required | Rules |
|---|---:|---|
| `country` | Conditional | Country name or two-letter code. Authenticated users always use their stored country. |
| `q` / `search` | No | Maximum 80 characters. |
| `limit` | No | Default 20; range 1–100. |
| `offset` / `start` | No | Default 0; range 0–10,000. |

Only active locations are returned. Ordering is stable: `sort_order`, `location`, then document ID. The query is backed by the production composite index `(country, is_active, sort_order, location)`.

Response data:

```json
{
  "schema_version": "1.1",
  "country": {"id": "Kenya", "name": "Kenya", "code": "KE", "flag": "🇰🇪"},
  "locations": [
    {"id": "abc123", "name": "Nairobi", "country": "Kenya", "sort_order": 1}
  ],
  "pagination": {
    "limit": 20,
    "offset": 0,
    "returned": 1,
    "has_more": false,
    "next_offset": null
  }
}
```

Stable input failures:

- `INVALID_COUNTRY`
- `INVALID_LIMIT`
- `INVALID_OFFSET`
- `INVALID_SEARCH_QUERY`
- `RATE_LIMIT`
- `CONFIG_ERROR`

## Account preferences

`get_my_preference` and `update_my_preference` remain under `aos.api.v1.accounts`. They use the same validators and serializers as Localization.

Updates may contain any non-empty subset of `country`, `currency`, and `language`. Omitted values are preserved, and row-level locking prevents concurrent partial updates from losing another field change. Currency and language remain editable when country is market-locked. Country changes return `MARKET_LOCKED` once seller ad activity exists.

All user-preference readers use one five-minute cache. Direct DocType writes, account updates, inserts, and deletes invalidate that cache.

## Mobile integration

- Load selectable values from `get_locale_bundle`; do not hardcode countries, currencies, or languages.
- Persist and send canonical `id` values, not labels, symbols, or emoji flags.
- Prefer `resolve_locale_context`; retain compatibility with `resolve_preference_context` during migration.
- Paginate locations using `pagination.next_offset` until `has_more` is false.
- Read account preference state from `/me` or account endpoints, not the guest resolver.
- Branch on stable `error` values rather than message text.
