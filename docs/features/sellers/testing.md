# Testing

Seller coverage includes strict input validation, operating hours, pagination, sort allowlists, public identity, thin API boundaries, lifecycle/schema fields, optimistic locking, Media delegation, migration safety, aggregate ownership, Maps compatibility, rate-limit coverage, and stable HTTP mappings.

Real Frappe integration coverage additionally verifies:

- opaque status identity and canonical capabilities;
- storefront version increments and idempotent retries;
- stale-version rejection without mutation;
- public list/detail privacy;
- suspended and blocked Seller invisibility;
- cross-user banner rejection;
- direct storefront, lifecycle, and metric mutation denial.

Run:

```bash
bench --site <site> run-tests --app aos --module aos.api.sellers.tests
bench --site <site> run-tests --app aos
```

The complete application suite is required before deployment because Seller state is consumed by Ads, Verification, Reviews, Accounts, Chat, Shorts, Maps, Reports, and account deletion.
