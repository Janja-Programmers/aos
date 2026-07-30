# Maps security and privacy

- All inputs are allowlisted, bounded and normalized before provider or SQL work.
- Dynamic SQL values are bound parameters; optional query fragments are fixed allowlists.
- Seller mutations require authentication, active Seller status and a locked owner row.
- Blocked, suspended, deleted and deactivated sellers are omitted from public location reads and map points.
- Cache keys contain SHA-256 digests rather than raw searches, addresses or coordinates.
- Structured logs contain operation/provider/outcome metadata only.
- Provider URLs reject credentials, query strings, fragments and public IP addresses.
- Provider response logging is bounded and control-character sanitized.
- Exact public coordinates are returned only by explicit Seller map-location contracts; general Seller discovery continues to use lightweight location summaries.
