# Ads pricing and localization

Ad country is derived from an active `AOS Location` inside the seller's selected country. The seller posts in the independently selected preference currency. Buyer list, detail, and wishlist reads request display currency through the normal preference context.

## Conversion contract

- Same currency: original numeric amount and currency; conversion is available but `converted=false`, rate `1`.
- Different currencies with both positive effective rates: cross-rate conversion (`target/source`). The configured `base_currency` has an implicit rate of `1`, so it does not require its own `AOS Exchange Rate` row.
- Missing/invalid source or target rate: original numeric amount and source currency; `available=false`, null rate, and `reason=MISSING_EXCHANGE_RATE`.

The `price_conversion` object contains source, target, requested, and actual display currencies. Consumers must use the actual `display_currency` and must not label fallback amounts with `requested_currency`.
