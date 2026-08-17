# AOS API Documentation

This directory is the API entry point. It intentionally does **not** duplicate full feature contracts.

## Read in this order

1. [Versioning](versioning.md) — stable public namespace and compatibility rules.
2. [Complete API reference](reference.md) — every whitelisted route discovered from the repository, grouped by domain with HTTP-method and guest/session exposure.
3. [Feature documentation](../features/README.md) — request/response semantics, state machines, limits, errors, privacy and lifecycle rules.
4. [Internal/platform HTTP surfaces](internal.md) — signed callbacks, LiveKit webhook, private metrics and admin diagnostics.

## Public route shape

The stable client namespace is:

```text
/api/method/aos.api.v1.<domain>.<method>
```

Unversioned modules under `aos.api.<domain>` contain implementation code and are not supported public HTTP routes.

## Authentication note

The reference's **Decorator access** column reports what the `@frappe.whitelist` declaration says. `Guest allowed` does not mean every resource is publicly readable: domain policy can still require authentication, ownership, membership, block/privacy checks, signed callback authentication, or admin roles.

## Generated route inventory

`reference.md` and the generated endpoint-inventory sections inside feature `api.md` files are maintained from code by:

```bash
python ci/validate_api_documentation.py --write
```

CI validates them with:

```bash
python ci/validate_api_documentation.py
```

Do not manually add a second endpoint inventory elsewhere.
