# Account preferences API v1

Persisted country, currency, and language are authenticated account resources. Public locale master data and guest/request context remain under `aos.api.v1.localization`.

## Get current preference

`GET /api/method/aos.api.v1.accounts.get_my_preference`

Returns rich independent `country`, `currency`, and `language` objects plus `is_country_locked`.

## Update current preference

`POST /api/method/aos.api.v1.accounts.update_my_preference`

Accepts any non-empty subset of:

```json
{
  "country": "Kenya",
  "currency": "USD",
  "language": "en"
}
```

Omitted fields are preserved. Updates lock the preference row so concurrent independent field changes cannot overwrite each other. Currency and language changes never alter country or each other. Country changes return `MARKET_LOCKED` after seller ad activity exists; that lock does not prevent currency or language changes.

Successful writes clear the canonical user-preference cache. DocType-level cache invalidation also protects updates made through administrative tools or internal code.

Clients must send canonical IDs returned by `get_locale_bundle` and branch on stable `error` values rather than message text.
