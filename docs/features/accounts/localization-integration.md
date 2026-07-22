# Localization integration

Localization owns active countries, currencies, languages, locations, guest resolution, and effective request context. Accounts owns persisted preference rows and update authorization.

Accounts calls public validation functions and stores canonical IDs. Preference serialization is delegated back to Localization so API representations remain consistent. A preference update invalidates the account cache used by locale/market bootstrap.
