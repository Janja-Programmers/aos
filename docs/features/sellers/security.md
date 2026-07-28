# Security

- Request fields are allowlisted before database work.
- Dynamic sort values are allowlisted; values are parameter bound.
- Search wildcards are escaped.
- Limits and offsets are strict and bounded.
- Public reads filter inactive accounts, Seller status, self, and blocks.
- Storefront updates require authentication, ownership, active status, and a row lock.
- Lifecycle changes require a central service and explicit reason/source.
- Metrics are server controlled.
- Banner Media is owner- and purpose-checked.
- Errors use stable codes and do not expose raw exceptions.
- Logs hash Seller references and exclude account identifiers and storefront text.
