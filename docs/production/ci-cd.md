# CI/CD and controlled deployment

## Purpose

The repository separates validation from deployment:

- `.github/workflows/ci.yml` is the required, non-deploying validation gate.
- `.github/workflows/deploy.yml` accepts only a successful same-repository `push` CI run on `main`, creates an immutable release, deploys staging first, and enters the `production` GitHub Environment only after staging succeeds.
- All deployment runs share one non-cancelling concurrency group. Release, staging, and production independently reject an automatic release if its commit is no longer `main` HEAD. A superseded release must be revalidated rather than silently deploying out of order.
- Manual `workflow_dispatch` is explicitly dry-run only and cannot deploy either staging or production.

Production approval, hosts, SSH material, the reviewed remote release-applier path, and separately verified OCI build artifacts remain operator supplied. The repository does not contain production credentials or destinations.

## Compatibility and pinned tools

`ci/versions.env` is the execution manifest. CI uses the repository-pinned Python, Frappe, Bench, MariaDB, Redis, Actions, service images, ShellCheck, Prometheus, and Alertmanager versions. `ci/install-infra-tools.sh` verifies SHA-256 checksums before installing `promtool` and `amtool`.

The local developer runtime may differ; `TEST_RESULTS.md` records the exact runtime used for the delivered artifact.

## Required CI coverage

The required CI gate includes:

- Python compilation, Ruff for maintained/changed Python, formatting policy, data-file parsing, conflict/debug/whitespace checks, and clean-tree checks.
- Secret scanning, tracked-runtime-artifact checks, and lock-file credential checks.
- Dependency vulnerability audit and Semgrep.
- Eight isolated companion-service test jobs, including sensitive FastAPI 422 validation handling, stable downstream dispatch IDs, generation replacement, duplicate-active handling, and callback replay.
- A disposable Frappe/MariaDB/Redis site, migrations, patch-rerun coverage, and the complete AOS Frappe suite. `ci/assert_frappe_tests.py` fails when the outbox redispatch, callback atomicity, recovery, metrics, backup-readiness, or migration-preflight modules are missing.
- Backup discovery, encryption, encrypted-only local retention, cleanup, readiness, and strict rehearsal-policy tests.
- Prometheus config/rule validation, Alertmanager validation and linkage, scrape-target/absence alert coverage, Redis-metrics-backend alert coverage, Nginx callback-rate policy, systemd units, Docker Compose, shell syntax, ShellCheck, deployment dry runs, GitHub Actions pinning, and documentation paths.

CI does not treat unavailable validation tools as success: the infrastructure job installs the pinned Prometheus, Alertmanager, and ShellCheck tools before invoking their validators.

## Release artifact

The release job:

1. Checks out the exact successful CI commit.
2. Creates a Git archive.
3. Writes `release-manifest.json` (schema 2) with the full Git commit, archive SHA-256, every statically digest-pinned external Compose image, every runtime-resolved image variable, and **every source-built Compose service** with its Dockerfile, context, and a deterministic SHA-256 of the context files in that exact archive.
4. Reopens the archive during verification and independently recomputes the image inventory and source-build fingerprints; a missing service, extra service, or altered claim fails closed.
5. Uploads the archive and manifest as a bounded-retention Actions artifact.

The remote deployment verifies the archive checksum before applying it. The manifest's context fingerprints establish **source provenance, not OCI image digests**. In particular, an image built locally from a source context is not proven reproducible or approved merely because the source is hashed. The reviewed release-applier must record/verify the actual image IDs and ensure no floating/unapproved images are deployed. A further controlled build-and-promotion gate is required before production authorization. External variable-supplied images (such as Valhalla) must be resolved to digest-pinned references by the actual deployment configuration; the manifest explicitly records the requirement rather than inventing an image digest.

## OCI image-lock verification

The schema-2 release manifest fingerprints every Docker build context, but those fingerprints are **not** published OCI image digests. `scripts/deploy/image_lock.py` defines an independent, fail-closed promotion receipt for use by the reviewed image-builder and release-applier:

- `create MANIFEST BUILD_RECEIPTS_JSON RUNTIME_REFS_JSON OUT COMMIT` produces a lock bound to the **exact manifest bytes and commit**. Build receipts are keyed by unique `infra/...` contexts, not duplicated worker service names.
- `verify MANIFEST LOCK COMMIT` rejects missing/extra contexts, source fingerprint disagreement, mutable/non-digest image references, unexpected AOS image repositories, omitted runtime images, and cross-release reuse.
- `verify-registry MANIFEST LOCK COMMIT` additionally queries the registry through the repository-pinned `crane` CLI and requires every published reference to resolve to its declared manifest digest. Run this from the trusted image-publishing job with the necessary scoped registry read credentials.

Build/publish provenance remains a separate requirement: creating an image lock from manually supplied references does not prove that a given OCI binary was produced by a trusted builder, and registry existence alone does not prove correspondence to source. The publishing pipeline must bind its signed build attestations to the checked release commit and source fingerprint. This verification contract is added without automatic image publication or a deployment policy bypass. Production enablement remains unset pending a reviewed end-to-end image-promotion/release-applier implementation and staging rehearsal.

## GitHub Environments

Create `staging` and `production` Environments. Configure required reviewers on `production`; do not permit routine approval bypass. This is a required GitHub repository setting, not something the workflow can establish on its own. Verify actual reviewer protection in GitHub Settings before configuring production credentials.

**Production is disabled by default.** The protected `production` Environment must separately define `AOS_PRODUCTION_DEPLOYMENT_ENABLED=true` for an authorized release. An unset, blank, or any other value fails before SSH setup, preflight, or deployment. Do not set this variable during staging-only rehearsals. Keep human approval enabled even when this variable is set.

A newly configured repository has no implied deployment connection. The first staging deployment will fail closed with `Required variable is missing: DEPLOY_HOST` until the staging Environment has been explicitly configured. Keep staging and production destinations, credentials, and known-hosts trust records separate. Do not paste private keys or secrets into issues, workflow inputs, PRs, or chat.

Environment secrets:

- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_SSH_PRIVATE_KEY`
- `DEPLOY_KNOWN_HOSTS`

Environment variables (configure separately for each environment):

- `AOS_PRODUCTION_DEPLOYMENT_ENABLED` (production only; leave unset until a reviewed, authorized production release)
- `REMOTE_BENCH_ROOT`
- `FRAPPE_SITE`
- `REMOTE_RELEASE_ROOT`
- `REMOTE_APPLY_RELEASE_PATH`

`REMOTE_APPLY_RELEASE_PATH` must be one reviewed absolute executable path with no embedded arguments. Its only responsibility is to apply the exact uploaded archive/manifest/commit. It must not select a floating branch or image tag. Arbitrary operator command text is not accepted.

## Repository-enforced migration

`scripts/deploy/deploy.sh` always performs this order:

1. Validate environment and immutable release manifest.
2. Upload and checksum-verify the exact release.
3. Invoke the reviewed release-applier path.
4. Invoke the deployed repository's `scripts/deploy/run-migrate.sh` unconditionally.
5. Return success only after migration succeeds.

The workflow runs `scripts/deploy/smoke.sh` only after `deploy.sh` returns successfully. Migration therefore cannot be omitted by the release-applier implementation.

`scripts/deploy/run-migrate.sh` writes an atomic mode-`0600`, sanitized migration-failure marker when `bench migrate` fails and clears it only after a later successful migration. CI simulates both failure and success and rejects a workflow or deployment script that can bypass the guarded migration path.

## Production gates

Before deployment, remote preflight checks:

- Production configuration and secret-placeholder policy.
- Database connectivity and installed application.
- Pending patch visibility and any previous migration-failure marker.
- Required outbox table and columns.
- Filesystem headroom, available database metadata, long-running transactions, and pending metadata locks.
- Verified encrypted backup freshness, encrypted offsite evidence, and full restore-rehearsal evidence.
- Outbox backlog/dead-letter state and visible workers.

The preflight is a blocker detector, not proof that every custom patch is non-destructive. A human must review pending patches and schema-changing code before production approval.

## Staging and production flow

1. A same-repository `main` push CI run completes successfully; CI runs from manual dispatches, pull requests, other repositories, or obsolete commits cannot trigger a deploy.
2. Release artifact and manifest are created only for the current `main` commit.
3. Staging preflight runs.
4. The exact release is applied to staging.
5. Repository-controlled guarded migration runs.
6. Operational health, job diagnostics, outbox diagnostics, and smoke checks run.
7. Production Environment approval is granted; the production job rechecks that the commit is still current `main` HEAD before contacting a host.
8. Production preflight reruns all production-only backup/encryption/rehearsal gates.
9. The exact release is applied and guarded migration runs.
10. Post-migration health, job, outbox, and smoke checks run.

Any failure stops the current environment and prevents later smoke or production stages.

## Rollback

Rollback requires:

- Exact previous Git commit.
- Exact previous release manifest and image digests.
- Explicit verified backup identifier.
- Knowledge of whether the failed release's migration completed.
- Protected deployment access.

Use `scripts/deploy/rollback.sh` with the exact prior `aos-release.tar.gz` and `release-manifest.json`; the local and remote artifact checksums must match before the reviewed `REMOTE_APPLY_RELEASE_PATH` is invoked. Live application-only rollback requires `ROLLBACK_APPROVED=true` and `ROLLBACK_DB_DECISION=application-only`. Arbitrary remote command text is prohibited. Do not roll back to a branch name or mutable image tag. Application rollback and destructive database restore are separate decisions; rollback does not run migrations or restore the database. Follow `docs/production/runbooks/deployment-and-rollback.md`.

## Local validation

```bash
python ci/validate_deployment.py .
python ci/validate_monitoring.py .
python ci/validate_nginx_policy.py .
ci/validate-infrastructure.sh
```

The last command requires the repository-pinned runtime plus ShellCheck, Nginx, `promtool`, and `amtool`.
