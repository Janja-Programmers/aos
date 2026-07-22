# Accounts testing

Feature-owned tests live in `aos/api/accounts/tests/`; DocType invariants remain beside their DocTypes. Generic repository contracts remain in `aos/tests/`.

Coverage includes pure validation, public identity, private/public serializer separation, strict profile patches, preference partial updates and locking, Media authorization, lifecycle idempotency, migration safety, Auth compatibility, and consumer privacy. External storage, OAuth providers, Redis, and sessions are mocked deterministically where integration is not the subject.

Run dependency-light tests with:

```bash
python -m unittest -v aos.api.accounts.tests.test_validation
```

Run the full suite in Bench:

```bash
bench --site <site> run-tests --app aos
```
