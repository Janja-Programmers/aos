# Testing and local CI

## Pinned toolchain

AOS targets Python `3.14.6` exclusively (`>=3.14,<3.15`). The framework and
toolchain pins are machine-readable in `ci/versions.env`: stable Frappe
`v16.27.1` at commit `f33ac3f00ab818e21b25ddbec93efb653fd9aa1b`,
Frappe Bench `5.31.0`, and Node `24.18.0`. Set `AOS_PYTHON` to an executable
for Python 3.14.6 when it is not available as `python3.14`.

The local checks write disposable environments and reports under
`${TMPDIR:-/tmp}/aos-ci-${UID}` by default. Override `AOS_CI_WORKDIR` or
`AOS_CI_ARTIFACTS` when needed. None of these commands removes sites,
databases, volumes, or model data.

## Commands

Run `make help` for the current interface. Common commands are:

```bash
make fast
make hygiene
make audit
make semgrep
make fastapi
make fastapi-service SERVICE=image-search
make compat
make compat-service SERVICE=translation
make compose
make infra
make full-ci
```

`make fastapi` uses reduced, exactly pinned test dependencies so no model is
downloaded or initialized. `make compat` is deliberately separate: it installs
each service's complete hash-locked production dependency graph, runs
`pip check`, and imports its application/configuration entry points under
Python 3.14.6.

Each service has a documented line-coverage floor in
`ci/coverage-floors.env`; the test runner passes that value to pytest-cov and
fails below it. The current floors cover the HTTP/configuration and external
boundary paths without pretending that unit tests exercise ML models or
providers. Queue-backed services also exercise an isolated worker success and
failure path. Background removal, image search, and translation have no queue
or HMAC contract, so their applicable processor/vector/translator boundary is
tested instead.

To test Frappe against an existing development or disposable site:

```bash
export AOS_PYTHON=/path/to/python3.14
export AOS_BENCH_PATH=/path/to/frappe-bench
export AOS_FRAPPE_SITE=site-name
export AOS_FRAPPE_RUN_MIGRATE=1  # optional; defaults to no migration locally
make frappe
```

The command verifies the Bench Frappe checkout is exactly the pinned commit,
sets `allow_tests`, performs an AST-based collection assertion, runs
`bench --site <site> run-tests --app aos`, and rejects output that does not
prove at least one test ran. CI creates a fresh site and always migrates it.

## Reports

JUnit and coverage XML are written under `artifacts/fastapi/<service>` within
the CI work directory. Compatibility evidence is under
`artifacts/compatibility/<service>`. pip-audit, Semgrep, Compose, and Frappe
logs use adjacent directories. GitHub retains uploaded artifacts for 14 days,
including on failures. Frappe logs are sanitized before upload.

## Updating dependencies and locks

Edit the exact versions in `pyproject.toml`, `ci/requirements/*.in`, or a
service `requirements.txt`, then install `uv 0.11.28` and run:

```bash
AOS_PYTHON=/path/to/python3.14 ci/update-locks.sh
make fast
make audit
make fastapi
make compat
```

The script regenerates hash-locked root, CI, service production, and reduced
service-test locks. Do not hand-edit generated lock files. Test-only
requirements remain separate from production requirements and use exact
versions.

## Adding a FastAPI service

Add the service name to all three locations, failing the review if any differ:

1. `ci/service-matrix.txt` (local source of truth).
2. The `fastapi-unit-tests` matrix in `.github/workflows/ci.yml`.
3. The `python314-runtime-compatibility` matrix in the same workflow.

Also provide `requirements.txt`, `requirements.lock`,
`requirements-test.txt`, `requirements-test.lock`, a digest-pinned Python 3.14 Dockerfile, an importable
`app.main`, and collected tests under `tests/`. Update Compose with a
healthcheck and loopback host binding when it publishes a private port.

## Prerequisites and limitations

Repository checks need Git. Compose validation needs Docker Compose v2 but does
not start services or download image layers. A reproducible local Compose tool
set can be installed with `ci/install-compose-tools.sh /tmp/aos-compose-tools`;
then set `AOS_COMPOSE_BIN=/tmp/aos-compose-tools/bin/docker-compose` and put its
`bin` directory on `PATH`. The installer verifies the exact Docker Compose and
crane archive checksums from `ci/versions.env`.

Infrastructure validation needs OpenSSL and Bash. Install its exact ShellCheck
and source-built Nginx toolset with
`ci/install-infra-tools.sh /tmp/aos-infra-tools`, put its `bin` directory on
`PATH`, and set `NGINX_MIME_TYPES` to the installed Nginx `conf/mime.types`.
The validation renders only synthetic values into a temporary tree and never
touches host Nginx or systemd configuration.

Semgrep rule checkout, Python package installation, vulnerability advisory
data, and remote image-manifest queries require network access. Unit tests
deliberately prohibit outbound socket connections. They do not prove model
inference, provider integration, or production service availability. Photon
is absent from maintained Compose until a published immutable image digest can
be verified; the source build remains documented in `ARTIFACTS_MANIFEST.md`.
