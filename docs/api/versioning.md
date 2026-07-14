# AOS API Versioning

AOS exposes public backend APIs through explicit versioned Frappe method namespaces.

## Current public API version

The current public API version is **v1**.

Public method paths must use:

```text
/api/method/aos.api.v1.<feature>.<method>
```

Example:

```text
POST /api/method/aos.api.v1.auth.login
GET  /api/method/aos.api.v1.auth.me
POST /api/method/aos.api.v1.auth.logout
```

## Unversioned API modules

Unversioned modules such as:

```text
aos.api.auth
aos.api.ads
aos.api.media
```

are internal implementation modules and are **not** the public HTTP contract. They must not expose whitelisted public endpoint wrappers.

## Implementation layout

```text
aos/api/<feature>/...        internal implementation and helpers
aos/api/v1/<feature>/...     public v1 whitelisted wrappers only
```

This keeps internal business logic independent from the public API contract. Future versions such as `aos.api.v2.*` can reuse stable implementation helpers or introduce new implementation code only where behavior changes.

## Client migration rule

Clients should centralize the method prefix:

```text
AOS_API_METHOD_PREFIX=aos.api.v1
```

Then build method names from the prefix instead of scattering hardcoded paths across the app.

## Breaking change policy

AOS is still in development. The final production contract is versioned from the start, so unversioned `/api/method/aos.api.<feature>.*` endpoints are not supported as public API paths.
