# Account preferences API v1

`get_my_preference` returns rich independent `country`, `currency`, and `language` objects plus `is_country_locked`. `update_my_preference` accepts any non-empty subset of those fields. Omitted fields are preserved.

Currency and language changes never alter country or each other. Country changes return `MARKET_LOCKED` after seller ad activity exists; that lock does not prevent currency or language changes. Successful writes clear the cached user market context.

Clients must send canonical IDs from the locale bundle and branch on stable `error` values rather than message text.
