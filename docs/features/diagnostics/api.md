# Diagnostics API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_backup_readiness_status` | GET/POST | Session required | Admin |
| `get_job_monitoring_status` | GET/POST | Session required | Admin |
| `get_operational_health_status` | GET/POST | Session required | Admin |
| `get_production_config_status` | GET/POST | Session required | Admin |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

Base method prefix: `aos.api.v1.diagnostics.`. Every endpoint is whitelisted for GET/POST transport but performs a server-side admin check. Only a user with effective Read permission on `AOS Settings` may receive the redacted report (System Manager has this by default in the source DocType permissions).

- `get_production_config_status` — validates production configuration without returning secrets.
- `get_operational_health_status` — returns the redacted operational-health report.
- `get_job_monitoring_status` — returns background/outbox/job-monitoring readiness.
- `get_backup_readiness_status` — validates backup/restore-readiness configuration and state.

Unauthorized callers receive `PERMISSION_DENIED`.
