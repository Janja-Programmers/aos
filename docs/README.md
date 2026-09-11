# AOS Backend Documentation

This directory is the documentation entry point for the AOS backend. The repository code is authoritative; documentation is organized so each concern has one canonical owner and API inventories are validated against the actual whitelisted code.

## Start here

| Need | Go to |
|---|---|
| Understand a product/domain workflow | [Feature documentation](features/README.md) |
| Find an HTTP/Frappe endpoint | [API guide](api/README.md) → [complete API reference](api/reference.md) |
| Understand API versioning/compatibility | [API versioning](api/versioning.md) |
| Deploy or operate AOS | [Production documentation](production/operations.md) and [release checklist](production/release-checklist.md) |
| Run tests / local validation | [Development testing](development/testing.md) |

## Documentation ownership

AOS deliberately avoids maintaining the same contract in multiple places:

1. **Code is the ultimate source of truth.** Public wrappers live under `aos/api/v1/`; private implementation modules live under `aos/api/`.
2. **`docs/features/` is the canonical human documentation for domain behavior.** A production-hardened feature owns exactly one comprehensive Markdown document at `docs/features/<feature>/README.md`, covering ownership, dependencies, data model, lifecycle, public API, security/privacy, operations, integrations, retention, schema/migration notes, and validation. Older unhardened feature folders may still be split and must be consolidated when that feature is hardened.
3. **`docs/api/reference.md` is the complete route inventory.** It is generated from `@frappe.whitelist` declarations and contains every v1 route plus the private metrics routes. Do not hand-maintain a competing endpoint list.
4. **`docs/api/` is navigation and cross-cutting API policy, not a second set of feature contracts.** Historical domain pages remain only as compatibility pointers to their feature documentation.
5. **`docs/production/` owns deployment, companion services, monitoring, backup, security configuration, and runbooks.** Signed internal callbacks are listed in the API reference but their operational semantics live with the owning production service.

## Keeping documentation accurate

Run:

```bash
python ci/validate_api_documentation.py
python ci/validate_doc_paths.py
```

`make fast` also validates the API documentation contract. If a whitelisted endpoint is added, removed, renamed, changes HTTP methods, or changes guest/session exposure, the API documentation check must fail until the code-derived inventory is regenerated and the owning feature documentation is updated.

To regenerate only the code-derived endpoint inventories after an intentional API change:

```bash
python ci/validate_api_documentation.py --write
```

Review the resulting documentation diff; generation keeps the inventory accurate but does not replace human documentation of business semantics.
