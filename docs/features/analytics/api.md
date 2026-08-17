# Analytics Pipeline API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `handle_callback` | POST | Guest allowed | Signed callback |
| `track_event` | POST | Guest allowed | Client |
| `track_events` | POST | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

Base method prefix: `aos.api.v1.analytics_pipeline.`.

## Client ingestion

### `track_event` — POST — guest decorator

Accepts one bounded analytics event. The backend validates event shape, string lengths, JSON depth/size, and supported scalar/object structures before persistence/enqueue. When the request has an authenticated user, identity is derived from the server session; callers cannot choose another user's identity. Guest events remain anonymous.

### `track_events` — POST — guest decorator

Accepts a bounded batch of analytics events and applies the same validation and server-derived identity rules to each event. Batch size and aggregate payload are bounded to prevent anonymous ingestion from becoming an unbounded write/queue path.

Analytics is telemetry only. Clients must never treat successful ingestion as proof that a business action occurred.

## Private callback

### `handle_callback` — POST — signed service callback

This route is callable without a Frappe session because the analytics companion authenticates using the AOS callback HMAC contract (timestamp + raw request body). It is **not** an anonymous public-client API. See [the production analytics-pipeline service](../../production/analytics-pipeline-service.md).
