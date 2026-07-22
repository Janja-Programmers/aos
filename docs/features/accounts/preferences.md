# Account preferences

`AOS User Preference` remains owned by Accounts. Localization validates and serializes active master-data records but does not mutate account rows.

Updates are partial and row-locked. Updating language does not overwrite currency, country, or location. A location must be active and belong to the effective country. Country is immutable after seller activation; language, currency, and permitted location remain independently editable.

Authenticated GET and `/me` retain a compatibility repair path for legacy missing rows. New account creation and login bootstrap create required rows normally. Cache keys hash internal user identifiers and writes invalidate the cache.
