# Secrets and Production Configuration Runbook

## Ownership and permissions

- Root or a dedicated deployment account owns `/etc/aos/*.env` and credential files.
- Runtime service accounts receive read access only to the secrets they need.
- Private keys and service-account JSON use mode `0600` or stricter.
- Example or `.dist` credential files must never be mounted in production.
- Companion services remain private; callbacks and dispatches use separate HMAC secrets.

Validate before every production migration:

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.utils.production_config.assert_production_config_ready
```

The gate rejects development mode, placeholder values, unsafe metrics/alerting configuration, disabled backup encryption, example Firebase credentials, broad credential permissions, and unsafe controlled-deployment declarations. Output names variables and remediation but never values.

## Rotation procedure

1. Inventory consumers and choose a maintenance/overlap window.
2. Create the new secret in the managed source; never paste it into Git, chat logs, metrics, or markers.
3. For paired HMAC secrets, deploy verifier support/new worker secret in a coordinated sequence that avoids unsigned callbacks.
4. Restart only affected services and run readiness/signed-contract smoke tests.
5. Revoke the old credential and verify logs contain no authentication failures.
6. Record owner, rotation date, expiry, and recovery contact in the operator system, not the repository.

For backup age identities, create a new recipient, temporarily encrypt to an approved recipient set during transition, prove decrypt/restore in rehearsal, then retire the old identity after retention obligations are met.

## Managed secret sources

The repository is provider-neutral. Suitable integrations include Vault Agent-rendered files, AWS Secrets Manager, Google Secret Manager, Azure Key Vault, Kubernetes secrets with external-secrets, or a host-managed encrypted secret store. Keep the application contract as environment variables or restrictive mounted files; do not add provider SDK dependencies to ordinary unit tests.
