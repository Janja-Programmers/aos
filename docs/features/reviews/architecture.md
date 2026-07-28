# Architecture

`aos.api.v1.reviews` contains stable Frappe wrappers. It removes the framework-owned `cmd` transport field and delegates to endpoint implementations under `aos.api.reviews`. Endpoint modules perform authentication and operation-specific rate limiting, then call `aos.services.reviews.ReviewService`.

The service layer is split into:

- `validation.py`: strict mass-assignment, rating, text, pagination, Media IDs and reporting rules.
- `eligibility.py`: target resolution, seller state, self-review, blocking, duplicate and communication evidence.
- `service.py`: create, update, withdrawal, listing, detail, reactions and reports.
- `serializers.py`: public-safe and owner-safe representations using Accounts identity primitives.
- `aggregates.py`: canonical ad/seller recomputation and bounded reconciliation.
- `observability.py`: bounded structured events without review text or PII.
- `api.py`: safe exception-to-envelope mapping and transaction rollback on handled failures.

The service never commits. Frappe owns request transactions. Moderation jobs are persisted with the domain mutation and dispatched through the existing transactional outbox.
