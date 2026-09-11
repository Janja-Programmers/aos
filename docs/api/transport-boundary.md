# Canonical Public API Transport Boundary

All production-hardened public AOS v1 feature wrappers must delegate through:

```python
from aos.api.shared.transport import execute_endpoint
```

The canonical boundary has one responsibility: separate framework-owned Frappe RPC metadata from client-controlled request fields before the domain handler performs strict validation.

## Required wrapper pattern

A normal endpoint uses:

```python
@frappe.whitelist(methods=["POST"])
def endpoint(**kwargs):
    return execute_endpoint(endpoint_impl, kwargs)
```

The executor strips only known Frappe transport metadata. Today that set is exactly `cmd`, which Frappe injects while dispatching `/api/method/...`. Every other field remains visible to the domain implementation so canonical unknown-field validation continues to fail closed.

Do not strip arbitrary underscore-prefixed fields, headers, request keys, or unknown values at this layer. Doing so would silently create compatibility aliases and weaken strict request contracts.

## Domain-specific unexpected-error policy

Transport normalization and unexpected-error policy are separate concerns. A feature that deliberately converts unexpected exceptions to a stable public error may layer a callback on the same executor:

```python
return execute_endpoint(
    endpoint_impl,
    kwargs,
    on_unexpected_exception=feature_exception_policy,
)
```

Authentication uses this form so unexpected failures still roll back pending writes, log secret-safe diagnostics, and return its stable `SERVICE_UNAVAILABLE` response. The exception policy must not implement its own transport-field filtering.

## Production-ready baseline

The following finalized features use this boundary for every public v1 endpoint:

- Localization
- Authentication
- Accounts
- Media
- Catalog

This is the baseline for feature hardening going forward. When another feature is hardened, its versioned wrappers must migrate directly to `aos.api.shared.transport.execute_endpoint`; transitional wrapper-specific transport helpers are not part of the production-ready standard.

## Tests

`aos.tests.test_finalized_api_transport_boundary` enforces that:

- all finalized public wrappers use the same executor;
- Frappe `cmd` is removed;
- genuine unknown client fields are preserved for domain rejection;
- Authentication exception handling remains layered on the shared executor rather than becoming a second transport implementation.
