# Search Ranking

## Overview

Search Ranking is an internal derived service for bounded marketplace candidate generation and ordering. It never decides whether an Ad is public. Frappe Ads eligibility and projection are authoritative after every ranking/search result.

## Responsibilities

The feature builds versioned search/index documents, submits durable indexing work, calls the configured ranking companion with bounded timeouts/retries, handles signed callbacks, returns related/search candidate ordering to Ads, and supports rebuild/recovery of disposable Redis/Qdrant/search state.

## Boundaries

Ads owns listing truth and public eligibility. Catalog, Sellers, Accounts, Verification, Social, Localization and Media own the fields/signals Search Ranking consumes. Search Ranking stores derived copies only and cannot mutate authoritative feature state.

## Architecture

```text
Authoritative Ads/domain change
        ↓
Search index job + transactional outbox
        ↓
private ranking/index companion
        ↓
signed callback
        ↓
AOS Search Index Job terminal state

Buyer search / Related Ads
        ↓
bounded candidate order
        ↓
authoritative Ads eligibility + projection
```

## Data Model

### AOS Search Index Job

Durable indexing work record. `name` uses Frappe `hash` naming and therefore has no naming-series field or shared sequence. `idempotency_key` is unique. Job state is callback-driven and generation/idempotency rules prevent stale work from becoming authoritative.

Manual state/target indexes are installed by `aos.patches.v1_0.install_marketplace_discovery_indexes` and reasserted after model synchronization.

## Fields

| Field | Type | Required | Indexed/Unique | Purpose |
|---|---|---:|---|---|
| `target_doctype` | Link → DocType | YES | — | Authoritative aggregate type. |
| `target_name` | Dynamic Link | YES | — | Authoritative aggregate identity. |
| `target_owner` | Link → User | NO | — | Owner snapshot used for lifecycle/recovery scoping. |
| `index_kind` | Select | YES | — | Ad/short/seller/user/category index family. |
| `action` | Select | YES | — | Upsert or delete action. |
| `source` | Data | NO | — | Bounded operation source label. |
| `status` | Select | YES | — | Durable job state. |
| `service_job_id` | Data | NO | — | Companion job correlation. |
| `idempotency_key` | Data | NO | unique | Deduplicates equivalent indexing work. |
| `attempt_count` | Int | NO | — | Dispatch/processing attempts. |
| `max_attempts` | Int | NO | — | Retry ceiling. |
| `last_error` | Small Text | NO | — | Sanitized terminal/retry diagnostic. |
| `document_json` | Long Text | NO | — | Derived index document. |
| `request_payload` | Long Text | NO | — | Safe companion request snapshot. |
| `response_payload` | Long Text | NO | — | Safe companion response snapshot. |
| `indexed` | Check | NO | — | Successful index marker. |
| `score` | Float | NO | — | Optional companion score. |
| `dispatched_at` | Datetime | NO | — | Dispatch timestamp. |
| `started_at` | Datetime | NO | — | Processing timestamp. |
| `callback_received_at` | Datetime | NO | — | Callback timestamp. |
| `completed_at` | Datetime | NO | — | Terminal timestamp. |

## API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `handle_callback` | POST | Guest allowed | Signed callback |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

Search Ranking has no frontend/Postman candidate-search API. Candidate generation is consumed internally by Ads services.

`aos.api.v1.search_ranking.handle_callback` is `POST`, guest-routable only because the private companion authenticates with the signed callback contract. It accepts the signed JSON body defined by callback security, validates `X-AOS-Search-Callback-Signature`, locks/updates the referenced `AOS Search Index Job` atomically, and returns `{job_id,target_doctype,target_name,status,action}`. Signature failures fail closed; stale/duplicate callback conflicts return stable callback conflict errors.

Related Ads is exposed to clients only as `aos.api.v1.ads.related_ads`.

## Cross-feature Dependencies

Search Ranking consumes Ads documents/eligibility inputs, Catalog metadata, Seller/Account/Verification eligibility signals, optional Social ranking signals, Localization geography, and Media-derived image-search identities. It uses the shared transactional outbox and Redis/Qdrant/ranking companion as derived infrastructure.

## Transaction / Concurrency Model

Index jobs use unique idempotency keys and hash names. Job callbacks execute through the shared atomic callback boundary and reject stale/conflicting state transitions. Generation-aware derived writes prevent delayed work from overwriting a newer generation. External companion calls are not held inside authoritative Ads database locks/transactions.

## Caching

Redis/Qdrant/search companion state is derived, versioned and rebuildable. It is never sufficient for public visibility. Ads always rechecks database state before projection.

## Performance / Scalability

Candidate sets, retries, payloads and page headroom are bounded. Indexing is asynchronous. Hash naming removes sequence contention from high-write job creation. Multi-node workers coordinate through database/outbox/Redis state rather than process-local locks. Production capacity depends on measured DB, queue, Redis, Qdrant and companion throughput under realistic load.

## Testing

Coverage includes callback signing/state conflicts, job idempotency, index generation/recovery, Related Ads fallback/eligibility, discovery architecture contracts, and internal job naming. Focused execution requires a configured Frappe bench; the full release gate is `bench run-tests --app aos`.
