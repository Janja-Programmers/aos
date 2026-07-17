# Localization API v1

AOS treats country, currency, and language as independent preferences. Country scopes marketplace content and valid ad locations; currency controls posting and display conversion; language controls localized UI/content. No preference implies another.

## Locale bundle

`GET /api/method/aos.api.v1.localization.get_locale_bundle`

Returns every Frappe `Country`, only enabled Frappe `Currency` and `Language` rows, and validated defaults from `AOS Settings`. Country flags are generated from the two-letter country code. Language `flag` is returned when that core field exists. Invalid or missing defaults produce `CONFIG_ERROR`.

Each country is `{id,name,code,flag}`; each currency is `{id,code,symbol,name,enabled,is_default}`; each language is `{id,code,name,flag,enabled,is_default}`. `defaults` contains canonical country, currency, and language IDs.

## Resolve preference context

`GET|POST /api/method/aos.api.v1.localization.resolve_preference_context`

Authenticated requests always use `AOS User Preference`. Guest values resolve independently: explicit request value, country proxy header (`X-Country-Code`/`CF-IPCountry`) or browser `Accept-Language` where applicable, then validated AOS defaults. `Accept-Language` is quality ordered and tries both the exact tag and its primary code (`en-US`, then `en`). Disabled and unknown languages are skipped. Invalid explicit inputs are rejected with `INVALID_COUNTRY`, `INVALID_CURRENCY`, `DISABLED_CURRENCY`, `INVALID_LANGUAGE`, or `DISABLED_LANGUAGE`.

Registration, Google signup, Apple signup, and missing-preference repair use the same guest resolver for missing fields. Explicit fields remain authoritative; headers fill only missing fields; AOS Settings is the final fallback.

The response contains rich `country`, `currency`, and `language` objects plus per-field `sources`: `request`, `geoip`, `accept_language`, `default`, or `user_preference`.

## Locations

`GET|POST /api/method/aos.api.v1.localization.get_locations`

Parameters: `country` (name or code; optional only when preference context can resolve it), `q`/`search`, and `limit` (default 20, maximum 100). The response is `{country, locations}`. Only active rows in that country are returned, ordered by sort order, location, then ID. A valid country with no configured locations returns an empty list.

`AOS Location` is unique by `(country, location)`. The same label may exist in different countries. Ad creation and DocType validation reject inactive, missing, or cross-country locations.

## Accounts and auth

`get_my_preference`, `update_my_preference`, auth responses, and `/me` use the same rich serializer. Updates may contain any non-empty subset of `country`, `currency`, and `language`. Currency and language remain editable when country is market-locked. Country changes return `MARKET_LOCKED` after seller ads exist.

Registration and new Google/Apple signup accept independent `country`, `currency`, and `language` inputs. Missing inputs use validated AOS defaults. Login and `/me` repair missing preference rows using the same rules. `/me` never returns a session ID.

## Price conversion fallback

Ad list/detail/wishlist price payloads include `price_conversion` with `source_currency`, `target_currency`, `requested_currency`, `display_currency`, `available`, `converted`, `rate`, and `reason`. Same-currency prices use rate `1` without conversion. Cross-currency conversion occurs only when both positive rate rows exist. If either rate is unavailable, AOS returns the original numeric price in the original currency, sets `available=false`, and returns `reason=MISSING_EXCHANGE_RATE`; it never relabels or partially converts the original amount.

## Mobile migration

- Replace hardcoded Kenya/currency/language lists with `get_locale_bundle`.
- Persist and send canonical `id` values, not display names or symbols.
- Expect rich objects in `/me` and account preference responses.
- Read `data.locations`; the locations response is no longer a bare array.
- Handle partial preference updates and the stable errors above.
- Use `price_conversion.display_currency`; show fallback UX when `available=false`.
