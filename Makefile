SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

.PHONY: help fast hygiene audit semgrep fastapi fastapi-service compat compat-service frappe compose infra full-ci

help:
	@printf '%s\n' \
	  'make fast                       Repository compile/lint/format/pre-commit checks' \
	  'make hygiene                    Tracked-file and secret scan' \
	  'make audit                      Audit every production and CI dependency lock' \
	  'make semgrep                    Run pinned Frappe/Python Semgrep rules' \
	  'make fastapi                    Unit-test all eight FastAPI services across their pinned runtimes' \
	  'make fastapi-service SERVICE=x  Unit-test one service' \
	  'make compat                     Install/check all production service locks across their pinned runtimes' \
	  'make compat-service SERVICE=x   Install/check one production service lock' \
	  'make frappe                     Run AOS tests in configured Bench/site' \
	  'make compose                    Validate Compose without starting services' \
	  'make infra                      Validate shell, Nginx, systemd, and paths' \
	  'make full-ci                    Run the local CI-equivalent sequence'

fast:
	ci/repository-quality.sh

hygiene:
	ci/repository-hygiene-and-secrets.sh

audit:
	ci/dependency-audit.sh

semgrep:
	ci/semgrep.sh

fastapi:
	ci/run-fastapi-tests.sh all

fastapi-service:
	@test -n "$(SERVICE)" || { printf 'ERROR: set SERVICE to an entry in ci/service-matrix.txt.\n' >&2; exit 1; }
	ci/run-fastapi-tests.sh "$(SERVICE)"

compat:
	ci/check-python314-compatibility.sh all

compat-service:
	@test -n "$(SERVICE)" || { printf 'ERROR: set SERVICE to an entry in ci/service-matrix.txt.\n' >&2; exit 1; }
	ci/check-python314-compatibility.sh "$(SERVICE)"

frappe:
	ci/run-frappe-tests.sh

compose:
	ci/validate-compose.sh

infra:
	ci/validate-infrastructure.sh

full-ci: fast hygiene audit semgrep fastapi compat frappe compose infra
