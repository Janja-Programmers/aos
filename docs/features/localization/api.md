# Localization

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_locale_bundle` | GET | Guest allowed | Client |
| `get_locations` | GET | Guest allowed | Client |
| `resolve_locale_context` | GET | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

This is the single authoritative backend document for the AOS Localization feature. Other feature documents may mention Localization ownership, but request/response contracts, data invariants, caching, migrations, and operational behavior are defined here.

## A. Feature overview

Localization provides the canonical country, currency, language, and country-scoped location vocabulary used by AOS. It also resolves the effective locale context for guest/bootstrap traffic and exposes the current authenticated user's persisted localization preference through the Accounts API.

AOS intentionally treats country, currency, and language as independent dimensions:

- **country** selects the marketplace/market context;
- **currency** selects the user's display/posting currency where consuming domains permit it;
- **language** selects the user's language preference;
- **location** is an optional active AOS location that must belong to the selected country.

Changing one dimension does not silently rewrite another. A country change may clear an existing location if that location does not belong to the new country. Seller ownership locks country changes because seller operations are market-bound; currency and language remain independently editable.

### Responsibilities owned by Localization

- validate and canonicalize Country, Currency, Language, and AOS Location values;
- expose the public locale bundle;
- resolve guest country/currency/language using explicit values, bounded request hints, and configured defaults;
- resolve authenticated effective locale context from the authenticated user's persisted preference;
- expose active, country-scoped AOS locations with bounded pagination;
- provide shared serialization/validation used by Auth, Accounts, Ads, Sellers, and other market-aware domains;
- maintain shared reference-data cache invalidation hooks;
- enforce Localization-owned database constraints and indexes.

### Related responsibilities owned elsewhere

- **Auth** owns creation of the initial `AOS User Preference` during registration/social-login/auth bootstrap. Localization/account reads do not create or repair missing rows.
- **Accounts** owns the authenticated `get_my_preference` and `update_my_preference` API endpoints and authorization to mutate the current user's persisted preference.
- **Foreign exchange** owns `AOS Exchange Rate`, `base_currency`, rate refresh jobs, and monetary conversion. Those are not Localization even though they consume currency identifiers.
- **Translation** owns text/message translation and translation-service configuration. Selecting a Language is Localization; translating content is not.
- **Maps/geocoding** owns coordinates, routing, and map search. `AOS Location` is a configured marketplace location vocabulary, not a geocoder.

### Major flows

1. **Application bootstrap:** client fetches `get_locale_bundle` and caches the returned reference vocabulary locally according to application policy.
2. **Guest bootstrap:** client calls `resolve_locale_context`; explicit values win, then safe hints, then validated AOS defaults.
3. **Authenticated bootstrap:** `resolve_locale_context` uses only the authenticated user's stored preference. Request locale overrides are rejected.
4. **Location selector:** client calls `get_locations`, paginating active locations for the effective country.
5. **Account preference read/update:** authenticated client uses `aos.api.v1.accounts.get_my_preference` and `aos.api.v1.accounts.update_my_preference`.
6. **Auth creation:** registration/social-login/auth bootstrap creates the one required preference row if it does not already exist.

## B. Architecture

```text
Client / consuming AOS domain
        ↓
aos.api.v1.localization thin GET wrapper
        ↓
aos.api.localization request validation / throttling
        ↓
aos.services.localization domain service
        ↓
validators / serializers / repository
        ↓
Frappe DocTypes + MariaDB and optional shared Redis cache
```

Persisted preference writes follow a related Accounts-owned path:

```text
Client
  ↓
aos.api.v1.accounts.update_my_preference
  ↓
Accounts auth + request boundary
  ↓
aos.services.user_preference_service
  ↓
row lock + Localization validators
  ↓
AOS User Preference controller invariants
  ↓
database transaction + post-commit cache invalidation
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| `aos/api/v1/localization/__init__.py` | Thin public v1 wrappers. Declares the only three public Localization routes and GET-only transport contract. |
| `aos/api/localization/bundle.py` | Locale-bundle boundary, strict fields, rate limit, safe error boundary. |
| `aos/api/localization/context.py` | Guest/authenticated effective-context boundary and ownership rules. |
| `aos/api/localization/locations.py` | Location list boundary and pagination orchestration. |
| `aos/api/localization/validation.py` | Public argument allowlists, bounded `limit`/`offset`, and literal search validation. |
| `aos/api/localization/throttle.py` | Reuses AOS shared rate limiting. Authenticated limiter keys use the raw session identity without extra account-status DB reads; guests use request IP. Localization reference reads fail open if the Redis limiter itself is temporarily unavailable. |
| `aos/services/localization/service.py` | Defaults, guest resolution, bundle assembly, and location-page domain logic. |
| `aos/services/localization/validators.py` | Canonical country/currency/language validation and bounded `Accept-Language` parsing. |
| `aos/services/localization/repository.py` | Localization-owned database reads. No public handler performs ad-hoc Localization SQL. |
| `aos/services/localization/serializers.py` | Minimal stable public payloads. Stored preference/context payloads use canonical IDs rather than re-querying master labels. |
| `aos/services/localization/cache.py` | Shared Redis reference-data cache and transaction-safe invalidation. Database remains authoritative. |
| `aos/services/user_preference_service.py` | Persisted per-user preference cache, row locking, partial update semantics, and location/country validation. |
| `aos/aos/doctype/aos_location/aos_location.py` | Direct DocType invariant enforcement for locations. |
| `aos/aos/doctype/aos_user_preference/aos_user_preference.py` | Direct DocType invariant enforcement for preferences, including Link validation and country/location consistency. |
| `aos/aos/doctype/aos_settings/aos_settings.py` | Validates Localization default settings and invalidates settings/reference cache on change. |
| `aos/patches/v1_0/install_localization_schema.py` | Clean-site post-model-sync installation of the final composite location uniqueness constraint, hot-read index, and defensive per-user preference uniqueness check. |

### Stateless / multi-node design

Correctness does not depend on module-level mutable state, process-local caches, in-memory locks, or sticky sessions. Authoritative state is in the database. Optional caches and rate-limit counters use Frappe's shared cache/Redis. Any application worker can serve the next request.

Cache invalidation for mutable reference/settings/preference data is performed immediately and again in an `after_commit` callback. The second delete prevents a different node from repopulating pre-commit data and retaining that stale value after the writer commits.

## C. Data model

### Relationship diagram

```text
Frappe User
   │ 1
   │
   └── 1 AOS User Preference
          ├── Country  ──────< AOS Location
          ├── Currency
          ├── Language
          └── AOS Location (optional; same Country, active only)

AOS Settings (Single)
   ├── default_country  ──> Country
   ├── default_currency ──> Currency
   └── default_language ──> Language
```

`AOS Exchange Rate` is deliberately outside this model; it belongs to the FX subsystem.

### AOS Location

**Purpose:** configured country-scoped marketplace location vocabulary used by selectors and preference validation.

**Ownership:** System Manager-managed reference data. Public clients can only read active rows through `get_locations`; they cannot write this DocType.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---|---|
| `name` | DocType name; hash autoname | System | Primary key | Stable opaque location ID returned to clients and stored by preferences. |
| `country` | Link → `Country` | Yes | Part of unique + read index | Market to which the location belongs. Canonicalized before Link validation. |
| `location` | Data | Yes | Part of unique + read index | Human-readable location label. Whitespace is normalized; max 140 characters. |
| `sort_order` | Int | No | Part of read index | Stable administrator-controlled primary display order. |
| `is_active` | Check | Yes by default (`1`) | Part of read index | Only active locations are publicly selectable or valid for a preference. |

**Database rules and indexes:**

- unique `(country, location)` via `unique_aos_location_country_location` or any exact equivalent unique index;
- read index `(country, is_active, sort_order, location)` via `idx_aos_location_country_active_order` or exact equivalent;
- no extra country-only or preference market/location indexes are installed by Localization; the schema keeps only indexes justified by current query paths.

**Lifecycle/deletion:** direct System Manager CRUD follows Frappe lifecycle. The public list never returns inactive rows. Preference links are validated by controller/service logic and direct DocType validation; a location must exist, be active, and belong to the stored country. This clean-install contract intentionally contains no historical-data repair path.

### AOS User Preference

**Purpose:** exactly one persisted localization preference per authenticated Frappe User.

**Ownership:** Accounts/Auth own row lifecycle; Localization owns value validation. Public clients cannot select another user identifier. Account endpoints derive ownership from the authenticated session.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---|---|
| `name` | `field:user` autoname | System | Primary key | Stable document identity derived from user. |
| `user` | Link → `User` | Yes | Unique | Owner of the preference. Database uniqueness enforces one row per user. |
| `country` | Link → `Country` | Yes | No standalone index | Canonical market. Country changes are denied once the account is country-locked. |
| `currency` | Link → `Currency` | Yes | No standalone index | Canonical enabled display/posting currency. |
| `language` | Link → `Language` | Yes | No standalone index | Canonical enabled language preference. |
| `location` | Link → `AOS Location` | No | No standalone index | Optional selected location; must exist, be active, and belong to `country`. Public/runtime preference access is by unique `user`, so a market/location preference index is not justified by the actual query paths. |

**Important invariants:**

- one row per `user`, enforced by a unique database constraint;
- all required Link fields pass normal Frappe Link validation; the controller calls `super()._validate_links()` after canonicalization;
- currency/language must be enabled if the current Frappe master exposes an `enabled` field;
- location is optional; when present it must be active and belong to the selected country;
- direct DocType writes and service/API writes apply the same Localization validators;
- preference updates are partial and serialized with a row-level `SELECT ... FOR UPDATE` lock to prevent lost updates;
- normal GET/update endpoints do not auto-create missing rows. Auth bootstrap is the single creation/repair authority.

**Deletion:** deleting a preference removes the persisted state and invalidates the per-user shared cache. Authenticated APIs that require it return `PREFERENCE_MISSING` until Auth creates the invariant row through an approved bootstrap flow.

### AOS Settings (Localization fields)

**Purpose:** singleton administrative source for deterministic guest/bootstrap defaults.

Only the following fields belong to Localization:

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---|---|
| `default_country` | Link → `Country` | Yes | Single-row setting | Final country fallback when request/hint resolution has no country. |
| `default_currency` | Link → `Currency` | Yes | Single-row setting | Final currency fallback. Must resolve to an enabled Currency when `enabled` exists. |
| `default_language` | Link → `Language` | Yes | Single-row setting | Final language fallback. Must resolve to an enabled Language when `enabled` exists. |

`base_currency` and `refresh_hours` live in the same DocType but belong to FX, not Localization.

The controller canonicalizes and validates Localization defaults before saving. Settings cache and Localization reference cache are invalidated immediately and post-commit.

### Frappe Country

**Purpose:** authoritative country master used by AOS market IDs.

Localization reads these meaningful fields:

| Field | Type | Required for AOS | Purpose |
|---|---|---:|---|
| `name` | Frappe document ID | Yes | Canonical AOS country ID returned/stored internally. |
| `code` | Data | Recommended | Two-letter country code accepted as an input alias and returned in the bundle; also used for emoji flag generation and proxy geo hints. |

Country names and two-letter codes may be accepted as input; the canonical stored/returned context ID is `Country.name`.

### Frappe Currency

**Purpose:** authoritative selectable currency vocabulary. FX rates are separate.

Localization reads:

| Field | Purpose |
|---|---|
| `name` | Canonical currency ID/code such as `KES` or `USD`. |
| `symbol` | Display metadata in the locale bundle. |
| `currency_name` | Display name when the field exists in the installed Frappe schema. |
| `enabled` | If present, only enabled currencies validate and appear in the bundle. |

Explicit currency input is trimmed and uppercased before validation. Canonical output is `Currency.name`.

### Frappe Language

**Purpose:** authoritative selectable language vocabulary.

Localization reads:

| Field | Purpose |
|---|---|
| `name` | Canonical persisted Language ID. |
| `language_name` | Human-readable display label returned in the locale bundle. It is **not** accepted as an explicit API identifier. |
| `language_code` | Language tag/code used by bundle metadata and `Accept-Language` resolution. |
| `flag` | Optional display metadata when present in the installed Frappe schema. |
| `enabled` | If present, only enabled languages validate and appear in the bundle. |

`Accept-Language` is bounded to 512 characters and 20 header entries. Parsed exact tags and primary fallbacks are resolved in one bounded database query rather than one query per candidate.

## D. Endpoint reference

### Response envelope

AOS method implementations return one of:

```json
{"ok": true, "message": "Human-readable message.", "data": {}}
```

or:

```json
{"ok": false, "message": "Safe human-readable message.", "error": "STABLE_CODE", "data": {}}
```

When called through Frappe `/api/method/...`, Frappe returns that AOS object under its normal top-level `message` transport property. Clients branch on `error`, never message text.

### 1. `aos.api.v1.localization.get_locale_bundle`

**Purpose:** one bootstrap payload containing public country/currency/language reference metadata and validated system defaults.

**Authentication:** Guest allowed. Same payload for authenticated and guest callers.

**Frontend usage:** application startup/bootstrap, country selector, currency selector, language selector, and mapping canonical IDs from preference/context payloads to labels/symbols/flags.

**HTTP:** GET only.

**Inputs:** none. Every request field is rejected.

Example:

```text
GET /api/method/aos.api.v1.localization.get_locale_bundle
```

Success `data`:

```json
{
  "schema_version": "2.0",
  "cache_ttl_seconds": 900,
  "countries": [
    {"id": "Kenya", "name": "Kenya", "code": "KE", "flag": "🇰🇪"}
  ],
  "currencies": [
    {"id": "KES", "code": "KES", "symbol": "KSh", "name": "Kenyan Shilling"}
  ],
  "languages": [
    {"id": "en", "code": "en", "name": "English", "flag": null}
  ],
  "defaults": {
    "country": "Kenya",
    "currency": "KES",
    "language": "en"
  }
}
```

**Stable errors:**

- `LOCALIZATION_UNKNOWN_FIELD` — any request field was supplied;
- `RATE_LIMIT` — shared limiter is available and the caller exceeds policy;
- `CONFIG_ERROR` — defaults or bounded master data are inconsistent;
- `INTERNAL_ERROR` — unexpected failure; details are logged server-side, never returned.

**Performance characteristics:**

- high-frequency bootstrap endpoint;
- shared Redis bundle cache key: versioned `aos:localization:bundle:*`;
- cache TTL: 900 seconds;
- warm cache: zero database master-data queries;
- cold rebuild: one bounded read for each master set plus default validation/settings reads; Country is capped at 300 rows, Currency at 300, Language at 1000;
- database is authoritative and cache failure falls back to bounded database reads;
- Country/Currency/Language/AOS Settings updates delete cache immediately and after commit;
- simultaneous cold misses may perform duplicate bounded rebuilds, but no lock is required for correctness and the rebuild contains no writes/long transactions;
- safe to retry because it is read-only and deterministic for a given committed configuration.

### 2. `aos.api.v1.localization.resolve_locale_context`

**Purpose:** resolve one canonical country/currency/language context.

**Authentication:** Guest allowed, with different semantics for authenticated sessions.

**Frontend usage:** guest application bootstrap and any flow that needs an effective locale before account preference exists. Authenticated clients may call it without locale fields to read effective IDs, but `/me` or `get_my_preference` is normally the richer account-bootstrap source.

**HTTP:** GET only.

**Inputs:**

| Parameter | Type | Required | Default | Limit/normalization | Description |
|---|---|---:|---|---|---|
| `country` | string | No for guest; forbidden for authenticated session | Hint/default resolution | ≤140 chars; Country name or two-letter code; canonical output is `Country.name` | Explicit guest country. |
| `currency` | string | No for guest; forbidden for authenticated session | AOS default | ≤16 chars; trimmed/uppercased; must be valid/enabled | Explicit guest currency. |
| `language` | string | No for guest; forbidden for authenticated session | Header/default resolution | ≤140 chars; configured `Language.name`/bundle `id` or `language_code`; enabled | Explicit guest language. Display `language_name` labels are not accepted identifiers. |

Unknown fields are rejected.

Guest precedence is independent per field:

1. explicit request value;
2. country only: `CF-IPCountry`, then `X-Country-Code`;
3. language only: bounded `Accept-Language`;
4. validated AOS Settings default for any still-missing field.

Geo headers are personalization hints only. They do not grant permissions or authorize private market data. Production proxies should overwrite/strip client-spoofable geo headers if those hints must reflect proxy geolocation.

Authenticated semantics:

- the active authenticated session resolves country/currency/language from its `AOS User Preference`;
- supplying any non-empty `country`, `currency`, or `language` returns `LOCALIZATION_OVERRIDE_NOT_ALLOWED` rather than silently ignoring it;
- missing persisted preference returns `PREFERENCE_MISSING`;
- stale/disabled/deleted sessions are handled by the shared optional-auth policy and do not gain private preference access.

Example guest request:

```text
GET /api/method/aos.api.v1.localization.resolve_locale_context?country=KE&currency=kes&language=en
```

Success `data`:

```json
{
  "schema_version": "2.0",
  "country": "Kenya",
  "currency": "KES",
  "language": "en",
  "sources": {
    "country": "request",
    "currency": "request",
    "language": "request"
  }
}
```

Possible source values are `request`, `geoip`, `accept_language`, `default`, and `user_preference`.

**Stable errors:** `LOCALIZATION_UNKNOWN_FIELD`, `LOCALIZATION_OVERRIDE_NOT_ALLOWED`, `INVALID_COUNTRY`, `INVALID_CURRENCY`, `DISABLED_CURRENCY`, `INVALID_LANGUAGE`, `DISABLED_LANGUAGE`, `PREFERENCE_MISSING`, `CONFIG_ERROR`, `RATE_LIMIT`, `INTERNAL_ERROR`.

**Performance characteristics:**

- authenticated preference is cached per user for 300 seconds in shared Redis; cache miss is one preference lookup;
- a fully explicit guest request validates only supplied masters and does not load defaults;
- `Accept-Language` candidates use one bounded database query, not N-per-candidate lookup;
- validated defaults use the shared defaults cache for 900 seconds;
- payload is ID-only and does not re-query Country/Currency/Language display metadata;
- read-only and safe to retry;
- no process-local correctness state.

### 3. `aos.api.v1.localization.get_locations`

**Purpose:** return one bounded page of active AOS locations for the effective country.

**Authentication:** Guest allowed.

**Frontend usage:** location selector, account preference screen, signup/onboarding market-location selection, seller/market flows that need the configured location vocabulary.

**HTTP:** GET only.

**Inputs:**

| Parameter | Type | Required | Default | Limit | Description |
|---|---|---:|---|---:|---|
| `country` | string | Guest: no. Authenticated: must be omitted. | Guest context; authenticated stored country | ≤140 chars through country validator | Explicit guest country. Authenticated callers cannot override stored market. |
| `q` | string | No | `""` | 80 chars | Literal normalized substring search; `%` and `_` are ordinary characters, not SQL wildcards. |
| `limit` | integer/string integer | No | 20 | 1–100 | Maximum rows returned. |
| `offset` | integer/string integer | No | 0 | 0–10,000 | Offset pagination position. |

Removed aliases `search` and `start` are not accepted.

Example:

```text
GET /api/method/aos.api.v1.localization.get_locations?country=KE&q=nai&limit=20&offset=0
```

Success `data`:

```json
{
  "schema_version": "2.0",
  "country": "Kenya",
  "locations": [
    {"id": "4f8e...", "name": "Nairobi", "country": "Kenya"}
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

**Stable ordering:** `sort_order ASC, location ASC`. `(country, location)` is unique, so this order is deterministic within a country. `sort_order` controls ordering internally and is not exposed as a public response field.

**Stable errors:** `LOCALIZATION_UNKNOWN_FIELD`, `LOCALIZATION_OVERRIDE_NOT_ALLOWED`, `INVALID_COUNTRY`, `INVALID_LIMIT`, `INVALID_OFFSET`, `INVALID_SEARCH_QUERY`, `PREFERENCE_MISSING`, `CONFIG_ERROR`, `RATE_LIMIT`, `INTERNAL_ERROR`.

**Performance characteristics:**

- one bounded location SQL query after effective-country resolution;
- returns `limit + 1` internally only to calculate `has_more`; at most 101 rows are hydrated;
- composite index `(country, is_active, sort_order, location)` supports country/active filtering and display order;
- substring `LOCATE(q, location)` is intentionally literal and not independently indexable; it evaluates only within the selected active country. If a future country contains an extremely large location vocabulary and substring search becomes a measured bottleneck, use a purpose-built search/index strategy rather than removing bounds;
- no reference cache is used for locations, so CRUD becomes visible from the database without cache invalidation races;
- offset is capped at 10,000; current reference-data scale does not justify keyset pagination;
- read-only and safe to retry.

### Related authenticated preference endpoints

These routes are owned by Accounts but are part of the finalized frontend Localization contract.

#### `aos.api.v1.accounts.get_my_preference`

- **HTTP:** GET only.
- **Authentication:** authenticated, active account.
- **Inputs:** none; request fields are rejected.
- **Rate limit:** 60/minute/user.
- **Mutation behavior:** none. Missing state returns `PREFERENCE_MISSING`; this GET never creates/repairs a row and never commits a transaction.
- **Response `data`:**

```json
{
  "country": "Kenya",
  "currency": "KES",
  "language": "en",
  "location": "4f8e...",
  "is_country_locked": false
}
```

`location` is nullable. IDs are intentionally minimal; client display metadata comes from `get_locale_bundle` and `get_locations`.

#### `aos.api.v1.accounts.update_my_preference`

- **HTTP:** POST only.
- **Authentication:** authenticated, active account.
- **Accepted fields:** `country`, `currency`, `language`, `location` only.
- **Required:** at least one accepted field.
- **Rate limit:** 20/minute/user.
- **Ownership:** user comes only from the session; there is no client `user` argument.
- **Concurrency:** row is selected `FOR UPDATE`; omitted values are preserved under the same transaction.
- **Country lock:** a country change for a seller-owned account returns `COUNTRY_LOCKED`; other fields can still be changed when otherwise valid.
- **Location:** empty string clears it; otherwise it must be active and belong to the resulting country. A country change automatically clears an existing location if it is not valid in the new country.
- **Transaction:** endpoint does not commit manually. Expected validation/business failures return without partial writes. If an unexpected exception is caught after a partial DB mutation, the endpoint explicitly rolls back before returning `INTERNAL_ERROR` because returning normally would otherwise let Frappe treat the request as successful.
- **Missing row:** returns `PREFERENCE_MISSING`; update does not create it.
- **Success:** returns the same minimal preference shape as `get_my_preference`.

## E. Frontend contract

### Canonical routes

```text
GET  aos.api.v1.localization.get_locale_bundle
GET  aos.api.v1.localization.resolve_locale_context
GET  aos.api.v1.localization.get_locations
GET  aos.api.v1.accounts.get_my_preference
POST aos.api.v1.accounts.update_my_preference
```

### Canonical client behavior

- Cache/map Country/Currency/Language display metadata from `get_locale_bundle` by each item's `id`.
- Treat canonical `country`, `currency`, and `language` in context/preference payloads as strings, not nested objects.
- Treat preference `location` as a nullable location ID string. Obtain its label from the locations response when needed.
- Send only canonical v1 parameter names.
- For authenticated locale/context/location reads, do not send country/currency/language overrides. Persist a desired change with `update_my_preference` first, then read the resulting state.
- Country input may be a configured Country name or two-letter Country code; store/use the canonical string returned by the backend.
- Currency input is canonicalized to the configured uppercase Currency ID.
- Language input must be a configured enabled `Language.name`/bundle `id` or its `language_code`; display labels are not request identifiers. Do not invent unsupported tags.
- `get_locations` uses `limit`/`offset`; follow `next_offset` only while `has_more` is true.
- Do not request `limit > 100` or `offset > 10000`.
- Branch on stable `error` codes and HTTP status, not English message text.
- GET calls are retry-safe. Preference update is a deterministic partial update; ordinary transport retry is safe in the sense that setting the same values twice converges to the same state, but clients should still avoid uncontrolled retry storms.

### Removed legacy contracts

#### Removed: `aos.api.v1.localization.resolve_preference_context`

- **Replacement:** `aos.api.v1.localization.resolve_locale_context`.
- **Frontend action required:** remove every call/reference to `resolve_preference_context`; it no longer exists.

#### Removed: POST access to Localization read endpoints

- **Replacement:** GET only for all three Localization routes.
- **Frontend action required:** send GET requests for bundle, resolver, and locations.

#### Removed: `get_locations.search`

- **Replacement:** `q`.
- **Frontend action required:** rename query key to `q`. Supplying `search` returns `LOCALIZATION_UNKNOWN_FIELD`.

#### Removed: `get_locations.start`

- **Replacement:** `offset`.
- **Frontend action required:** rename query key to `offset`. Supplying `start` returns `LOCALIZATION_UNKNOWN_FIELD`.

#### Removed: authenticated request locale overrides

- **Replacement:** no locale override arguments on authenticated resolver/location calls; update the stored account preference through `update_my_preference`.
- **Frontend action required:** stop sending authenticated `country`, `currency`, or `language` to `resolve_locale_context`; stop sending authenticated `country` to `get_locations`.

#### Removed: display-name / underscore language compatibility aliases

- **Old:** explicit language validation could fall back to `Language.language_name` and normalized underscore tags to hyphenated tags.
- **Replacement:** explicit requests use the bundle `id` / `Language.name` or configured `language_code`; `Accept-Language` continues to parse standards-style hyphenated tags separately.
- **Frontend action required:** send the backend-provided language `id` rather than a display label or an invented underscore-form locale.

#### Removed: rich resolver objects

- **Old:** nested country/currency/language display objects.
- **Replacement:** canonical ID strings plus `sources`.
- **Frontend action required:** map IDs to bundle metadata when display labels/symbols/flags are required.

#### Removed: rich country object in location-list response

- **Replacement:** `data.country` is the canonical country ID string.
- **Frontend action required:** map it through the bundle if a label/flag is required.

#### Removed: public `sort_order` in location items

- **Replacement:** location items contain only `id`, `name`, and canonical `country`; the backend still applies configured sort order internally.
- **Frontend action required:** do not read or sort by `sort_order`; preserve the deterministic order returned by the backend.

#### Removed: locale bundle schema `1.1` compatibility fields

- **Replacement:** schema `2.0`.
- **Removed item fields:** currency/language `enabled` and `is_default`.
- **Frontend action required:** all returned currencies/languages are already selectable/enabled; compare `data.defaults.currency` / `data.defaults.language` to item IDs when default highlighting is needed.

#### Removed: rich stored-preference objects

- **Old:** preference country/currency/language nested metadata and location nested object.
- **Replacement:** ID-only `country`, `currency`, `language`, nullable location ID, and `is_country_locked`.
- **Frontend action required:** use bundle/location lookups for display metadata.

#### Removed: read/update compatibility repair for missing preferences

- **Replacement:** Auth bootstrap owns preference creation; account reads/updates return `PREFERENCE_MISSING` when the invariant is absent.
- **Frontend action required:** do not rely on `get_my_preference` or `update_my_preference` to create state. Treat `PREFERENCE_MISSING` as an account/bootstrap integrity condition.

#### Removed: missing-`location` preference-schema compatibility

- **Replacement:** the current `AOS User Preference` schema always contains the nullable `location` Link field. Runtime reads no longer probe DocType metadata and writes no longer conditionally skip this field.
- **Frontend action required:** none beyond following the canonical preference response; this removes backend schema compatibility only.

## F. Important invariants

1. `Country.name`, `Currency.name`, and `Language.name` are the canonical stored IDs.
2. Guest country, currency, and language resolve independently; no implicit coupling changes one because another changed.
3. Validated AOS Settings defaults always resolve deterministically or the public bundle/context fails closed with `CONFIG_ERROR`.
4. Only enabled Currency/Language rows are selectable when those masters expose `enabled`.
5. Exactly one `AOS User Preference` exists per user; database uniqueness enforces it.
6. A preference location is optional; when present it exists, is active, and belongs to the preference country.
7. AOS Location `(country, location)` is unique.
8. Public location results contain active rows only and are deterministically ordered.
9. Authenticated public locale resolution never trusts client-supplied locale overrides.
10. Preference mutation never accepts a client-supplied owner/user ID.
11. Reads do not create missing persisted preference state.
12. Preference partial updates are row-locked and do not overwrite omitted fields.
13. Database state is authoritative. Redis/cache is an optimization, never the only copy of correctness-critical state.
14. Cache invalidation occurs after commit as well as immediately, preventing stale cross-node refill from surviving a committed update.
15. No correctness path relies on Python process-local mutable state or in-memory locks.

## G. Scalability and high availability

### Hot-path assessment

| Area / endpoint | DB queries/request | Cacheability | Index coverage | Write contention | Payload/resource bound | Horizontal-scaling safety | Concurrency risk | Main bottleneck |
|---|---|---|---|---|---|---|---|---|
| `get_locale_bundle` | Warm: 0 DB master reads. Cold: bounded defaults/master rebuild. | Excellent; shared Redis 900s | Core master PK/code lookups; full master lists are intentionally bounded | None | ≤300 countries, ≤300 currencies, ≤1000 languages | Stateless; shared cache | Benign duplicate cold rebuilds only | App/Redis response throughput on massive bootstrap bursts; edge caching can further reduce it. |
| Guest `resolve_locale_context` | Depends on supplied/hints; defaults can be 0 DB when cached; `Accept-Language` is one bounded query | Defaults shared-cacheable; request result not globally cacheable | Master identity lookups | None | 3 IDs + sources; header 512 chars/20 entries | Stateless | None | Repeated master validation for highly varied explicit guest traffic. |
| Auth `resolve_locale_context` | Preference warm cache: 0 preference DB reads after shared auth checks; miss: 1 preference read | Per-user preference shared cache 300s | unique `AOS User Preference.user` | None | Tiny ID-only response | Stateless | None | Shared auth/account-state checks and Redis/database latency. |
| `get_locations` | 1 bounded location query after context resolution | Query results not cached | `(country,is_active,sort_order,location)` | None | max 100 rows; offset max 10k; q max 80 | Stateless | None | Literal substring scan inside one country's active rows if that reference set becomes extremely large. |
| `get_my_preference` | Preference cache hit + seller-lock lookup; preference miss adds 1 DB read | Per-user shared cache 300s | unique user; Seller user lookup uses Seller schema/indexes | None | Tiny ID-only response | Stateless | None | Seller lock lookup. |
| `update_my_preference` | Write-path dependent; bounded validators + one row lock + document save | Preference cache invalidated | unique user; location PK/constraints | Serialized per preference row only | Four allowed fields | Multi-node safe via DB row lock/unique constraints | Lost update prevented; duplicate row prevented | Normal database write/validation latency; intentionally not a read hot path. |

### Shared cache strategy

**Reference keys:** versioned defaults and bundle keys in shared Frappe Redis, TTL 900 seconds.

**Preference key:** SHA-256-derived per-user key in shared Redis, TTL 300 seconds. User identifiers are not embedded in clear text in the cache key.

**Source of truth:** database/Frappe DocTypes.

**Invalidation:** Country, Currency, Language, and AOS Settings mutations invalidate reference cache; preference insert/update/delete invalidates that user's preference cache. Deletes happen once immediately and once after transaction commit.

**Cache failure:** Localization reference cache failure falls back to bounded database reads. User preference cache failure falls back to the database. Localization's application rate limiter also fails open for these read-only endpoints if Redis is unavailable, leaving the deployment's reverse-proxy/public API baseline as the outage-time abuse control. A broader Frappe deployment may still depend on Redis for framework functions; that infrastructure dependency must be monitored independently.

**Stampede behavior:** no cache lock is required for correctness. A simultaneous expiration/cold deploy can cause more than one worker to rebuild the same bundle, but each rebuild is bounded to small reference tables and performs no writes. If production telemetry shows burst amplification at the AOS scale target, configure CDN/reverse-proxy caching for the public bundle or add an infrastructure-supported single-flight policy; do not add process-local locks.

### Failure behavior

- invalid configuration fails closed with `CONFIG_ERROR`; it is never cached as valid data;
- optional cache failures do not silently substitute incorrect data;
- database/internal exceptions are logged server-side and return `INTERNAL_ERROR` without SQL, credentials, stack traces, or raw exception messages;
- guest geo/language hints that do not validate are ignored and deterministic defaults are used;
- preference/location invariant violations fail with stable validation/business codes rather than partial state;
- transaction-caught unexpected preference write failures are rolled back before the API returns.

### Application-level readiness

The code is designed for multiple web nodes/workers and does not require sticky sessions or process-local correctness state. Read hot paths are bounded, high-read/low-change reference data uses shared cache, user preference uniqueness/concurrency is database-enforced, and public client-controlled sizes are capped.

This is an application-architecture statement, not a claim that one server or a particular deployment can serve one million simultaneous users.

### Infrastructure-level capacity

Capacity must still be validated and sized using production-like load tests and observability. At minimum production planning must cover:

- Frappe web worker/node count and autoscaling strategy;
- reverse proxy/load balancer health checks and rate-limit baseline;
- Redis availability/capacity/latency;
- MariaDB capacity, connection pool/concurrency, replication/HA and failover strategy;
- backup/restore verification;
- monitoring/alerting for `CONFIG_ERROR`, `INTERNAL_ERROR`, Redis/database health, latency, and rate limiting;
- CDN/edge caching for `get_locale_bundle` if measured bootstrap traffic warrants it;
- regional network/deployment strategy for global latency;
- production data-volume and burst load tests before capacity claims.

## H. Testing

### Main Localization test modules

- `aos/api/localization/tests/test_api.py` — exact v2 response shape, GET-era request contract, removed aliases/endpoint, guest/auth resolution, pagination, rate limiting, error-safety.
- `aos/api/localization/tests/test_service.py` — canonical validation, header bounds, independent resolution, cache fallback/hit, bounded bundle failure, ID-only serialization.
- `aos/aos/doctype/aos_location/test_aos_location.py` — country canonicalization, label normalization, country-scoped uniqueness.
- `aos/aos/doctype/aos_user_preference/test_aos_user_preference.py` — Link integrity, canonicalization, unique user, cross-country/inactive location rejection, Auth bootstrap idempotency.
- `aos/api/accounts/tests/test_preferences.py` — partial-update preservation, current-schema hot path, strict read fields, `FOR UPDATE`, no read/update auto-creation, localization observability fields, rollback on unexpected mid-operation failure.
- `aos/tests/test_localization_database_contracts.py` — required exact indexes/unique constraints and idempotency of the clean-site Localization schema installation patch.

### Commands

From the Frappe bench containing this app:

```bash
bench run-tests --app aos --module aos.api.localization.tests.test_api
bench run-tests --app aos --module aos.api.localization.tests.test_service
bench run-tests --app aos --module aos.aos.doctype.aos_location.test_aos_location
bench run-tests --app aos --module aos.aos.doctype.aos_user_preference.test_aos_user_preference
bench run-tests --app aos --module aos.api.accounts.tests.test_preferences
bench run-tests --app aos --module aos.tests.test_localization_database_contracts
```

Full backend regression suite:

```bash
bench run-tests --app aos
```

For the intended clean/new-site deployment, install the app normally so model sync and the listed post-model-sync patches run. The Localization-specific schema patch is:

```text
aos.patches.v1_0.install_localization_schema
```

It installs only the final constraints/indexes required by the current contract and is idempotent. There is intentionally no legacy duplicate cleanup, stale-preference repair, old-index removal, or post-Accounts reconciliation path because this release targets a new site rather than an upgraded historical database. After installation, normal future releases should still run:

```bash
bench --site <site> migrate
```
