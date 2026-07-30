# Maps testing

Run targeted Maps validation and contract tests:

```bash
bench --site <site> run-tests --app aos --module aos.api.maps.tests.test_validation
bench --site <site> run-tests --app aos --module aos.api.maps.tests.test_maps_contracts
bench --site <site> run-tests --app aos --module aos.api.maps.tests.test_seller_location_api
```

Then run dynamic SQL, production configuration, operational health and the
complete application suite:

```bash
bench --site <site> run-tests --app aos --module aos.tests.test_dynamic_sql_safety
bench --site <site> run-tests --app aos --module aos.tests.test_production_config_validation
bench --site <site> run-tests --app aos --module aos.tests.test_operational_health
bench run-tests --app aos
```

Provider smoke tests must verify Nominatim search/reverse, Valhalla status and a
real route entirely inside the imported Kenya extract.
