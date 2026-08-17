# Analytics ingestion

Analytics ingestion accepts bounded client telemetry and durably hands it to the private analytics-pipeline companion through the transactional outbox. Telemetry is not an authorization or business-state source of truth.

Start with:

- [API](api.md) — client ingestion endpoints and the signed companion callback.
- [Production analytics pipeline](../../production/analytics-pipeline-service.md) — queue, worker, callback, retry, and operational behavior.
