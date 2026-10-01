# AOS backend

Fresh-deployment Frappe v16 application for a globally deployed marketplace with account, commerce, media and communications domains.

## Architecture

Frappe/MariaDB own canonical transactions and feature data. Redis/queues carry asynchronous work; a transactional outbox coordinates domain changes with Notifications, moderation, Analytics and external processing. Media owns durable object-storage references; companion HTTP services run on private infrastructure. Multiple web/workers must share database, cache, queue, secrets and object storage. No measured million-user capacity is implied.

The 26 current feature contracts have exactly one authoritative README each: [feature index](docs/features/README.md). Other global documentation: [docs index](docs/README.md), [API reference](docs/api/reference.md), [deployment](docs/production/deployment.md), [operations](docs/production/operations.md), [backups and restore](docs/production/backup-restore-verification.md), [load testing](docs/production/load-testing.md).

## Fresh installation

Pinned runtime and dependency versions: [CI versions](ci/versions.env) and [Python project metadata](pyproject.toml). Prepare the Frappe bench, private companion services and configuration from `.env.example` and the deployment runbook. Then install the app on a **new site**:

```bash
cd ~/frappe-bench
bench --site "$SITE" install-app aos
bench --site "$SITE" migrate
bench --site "$SITE" run-tests --app aos
```

Manual indexes are applied by schema-only post-model-sync installers and reasserted by `aos.migrate.after_migrate`. The canonical Reports reason master is installed through `aos.install.after_install`.

## Static audit and deploy gates

Run from `apps/aos`:

```bash
python ci/validate_repository.py .
python ci/validate_api_documentation.py
python ci/validate_doc_paths.py
python ci/validate_fresh_backend.py
```

Do not deploy without server-side Frappe suite, private service tests, load/failover trials, signed callback tests and backup/restore verification.
