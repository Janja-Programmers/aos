# Wishlist testing

Focused tests cover:

- explicit idempotent add/remove and legacy toggle behaviour;
- exact counter updates and lifecycle timestamps;
- own-ad rejection and unavailable-ad privacy;
- stale unavailable-item removal;
- recent default ordering, offset metadata, and cursor pagination;
- block filtering;
- safe hashed rate-limit keys;
- schema and migration idempotency;
- explicit sort precedence;
- invalid sort/cursor rejection before listing SQL; and
- v1 wrapper HTTP method contracts.

Run the focused suite on a disposable test site:

```bash
bench --site aos-test.local run-tests --app aos \
  --module aos.api.wishlist.tests.test_wishlist_api
bench --site aos-test.local run-tests --app aos \
  --module aos.tests.test_wishlist_contracts
bench --site aos-test.local run-tests --app aos \
  --module aos.tests.test_wishlist_database_contracts
bench --site aos-test.local run-tests --app aos \
  --doctype "AOS Wishlist"
```

Then run the complete application suite with `make frappe` or
`bench --site aos-test.local run-tests --app aos`.
