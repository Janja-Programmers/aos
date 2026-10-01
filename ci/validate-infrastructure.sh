#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version

require_command shellcheck
require_command nginx
require_command openssl

# Infrastructure validation must work independently of other CI targets.
# Provision an isolated Python environment using the existing hash-locked
# quality dependencies rather than relying on globally installed packages.
python_executable="$(python314)"

mkdir -p "${AOS_CI_WORKDIR}"

venv="${AOS_CI_WORKDIR}/infra"

"${python_executable}" -m venv --clear "${venv}"

"${venv}/bin/python" -m pip install \
    --disable-pip-version-check \
    --require-hashes \
    -r "${CI_ROOT}/ci/requirements/quality.lock"

python_executable="${venv}/bin/python"

# Validate maintained infrastructure shell scripts.
mapfile -t scripts < <(
    find "${CI_ROOT}/infra" "${CI_ROOT}/scripts/deploy" \
        -type f -name '*.sh' -print | sort
)

[[ "${#scripts[@]}" -gt 0 ]] \
    || die "No maintained infrastructure shell scripts were found."

for script in "${scripts[@]}"; do
    bash -n "${script}"

    if [[ "${script}" != *"/env.example.sh" && ! -x "${script}" ]]; then
        die "Maintained script is not executable: ${script#${CI_ROOT}/}"
    fi
done

shellcheck --severity=warning "${scripts[@]}"

# Verify that all maintained repository paths documented in CI exist.
while IFS= read -r relative; do
    [[ -e "${CI_ROOT}/${relative}" ]] \
        || die "Documented maintained path is missing: ${relative}"
done < "${CI_ROOT}/ci/maintained-paths.txt"

# Execute infrastructure validation using the isolated Python environment.
"${python_executable}" "${CI_ROOT}/ci/validate_systemd.py"
"${python_executable}" "${CI_ROOT}/ci/validate_doc_paths.py"
"${python_executable}" "${CI_ROOT}/ci/validate_monitoring.py" "${CI_ROOT}"
"${python_executable}" "${CI_ROOT}/ci/validate_companion_safety.py" "${CI_ROOT}"
"${python_executable}" "${CI_ROOT}/ci/validate_nginx_policy.py" "${CI_ROOT}"
"${python_executable}" "${CI_ROOT}/ci/validate_maps_routing.py" "${CI_ROOT}"
"${python_executable}" "${CI_ROOT}/ci/validate_deployment.py" "${CI_ROOT}"

# Validate the production Prometheus template using isolated CI files.
# Do not modify production configuration or require operational credentials.
if command -v promtool >/dev/null 2>&1; then
    prometheus_template="${CI_ROOT}/infra/monitoring/prometheus/prometheus.yml.example"
    prometheus_rules="${CI_ROOT}/infra/monitoring/prometheus/alerts.yml"

    prometheus_ci_root="$(
        mktemp -d "${AOS_CI_WORKDIR}/prometheus.XXXXXX"
    )"

    # Automatically remove temporary Prometheus configuration and credentials.
    trap 'rm -rf -- "${prometheus_ci_root}"' EXIT

    # Create a synthetic, CI-only metrics credential.
    # Never read actual deployment secrets during infrastructure validation.
    install -m 0600 /dev/null \
        "${prometheus_ci_root}/metrics.token"

    printf '%s\n' 'aos-ci-validation-only' \
        > "${prometheus_ci_root}/metrics.token"

    # Render a temporary copy of the production template.
    # Only deployment filesystem references are redirected.
    # Scrape jobs, authorization requirements, labels, alerts, and
    # monitoring behavior remain otherwise unchanged.
    "${python_executable}" - \
        "${prometheus_template}" \
        "${prometheus_ci_root}/prometheus.yml" \
        "${prometheus_rules}" \
        "${prometheus_ci_root}/metrics.token" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
rules_path = Path(sys.argv[3])
token_path = Path(sys.argv[4])

content = template_path.read_text(encoding="utf-8")

replacements = {
    "/etc/prometheus/rules/aos-alerts.yml": str(rules_path.resolve()),
    "/run/secrets/aos_metrics_token": str(token_path.resolve()),
}

for original, replacement in replacements.items():
    occurrences = content.count(original)

    if occurrences == 0:
        raise SystemExit(
            f"Expected Prometheus deployment reference is missing: {original}"
        )

    # AOS must maintain exactly one canonical alert-rule reference.
    if (
        original == "/etc/prometheus/rules/aos-alerts.yml"
        and occurrences != 1
    ):
        raise SystemExit(
            "Prometheus must declare exactly one canonical AOS alert-rule file."
        )

    content = content.replace(original, replacement)

output_path.write_text(content, encoding="utf-8")
PY

    # Native validation of the complete rendered configuration.
    # promtool checks referenced rule files and credential-file paths.
    promtool check config \
        "${prometheus_ci_root}/prometheus.yml"

    # Independently validate the actual source-controlled alert rules.
    promtool check rules \
        "${prometheus_rules}"
else
    printf '%s\n' \
        'promtool unavailable; strict repository Prometheus validator completed instead.'
fi

# Validate Alertmanager configuration with the native tool when available.
if command -v amtool >/dev/null 2>&1; then
    amtool check-config \
        "${CI_ROOT}/infra/monitoring/alertmanager/alertmanager.yml.example"
else
    printf '%s\n' \
        'amtool unavailable; strict repository Alertmanager validator completed instead.'
fi

# Prepare a temporary Nginx configuration for syntax validation.
nginx_root="$(mktemp -d "${AOS_CI_WORKDIR}/nginx.XXXXXX")"

nginx_mime_types="${NGINX_MIME_TYPES:-/etc/nginx/mime.types}"

[[ -f "${nginx_mime_types}" ]] \
    || die "Nginx mime.types is missing: ${nginx_mime_types}"

"${python_executable}" \
    "${CI_ROOT}/ci/render_nginx.py" \
    "${nginx_root}"

mkdir -p \
    "${nginx_root}/logs" \
    "${nginx_root}/acme"

# Generate temporary validation-only certificates.
# These are not deployment credentials.
for domain in api.invalid live.invalid maps.invalid files.invalid; do
    mkdir -p "${nginx_root}/certs/${domain}"

    openssl req \
        -x509 \
        -newkey rsa:2048 \
        -nodes \
        -days 1 \
        -subj "/CN=${domain}" \
        -keyout "${nginx_root}/certs/${domain}/privkey.pem" \
        -out "${nginx_root}/certs/${domain}/fullchain.pem" \
        >/dev/null 2>&1
done

# Redirect deployed filesystem references to the temporary test root.
find "${nginx_root}/conf.d" \
    -type f -name '*.conf' \
    -exec sed -i \
        -e "s#/etc/nginx/snippets/#${nginx_root}/snippets/#g" \
        -e "s#/etc/letsencrypt/live/#${nginx_root}/certs/#g" \
        -e "s#/var/log/nginx/#${nginx_root}/logs/#g" \
        -e "s#/var/www/letsencrypt#${nginx_root}/acme#g" \
        {} +

cat > "${nginx_root}/nginx.conf" <<EOF
user $(id -un);
error_log ${nginx_root}/logs/error.log;
pid ${nginx_root}/nginx.pid;

events {
    worker_connections 64;
}

http {
    map \$http_upgrade \$connection_upgrade {
        default upgrade;
        '' close;
    }

    include ${nginx_mime_types};
    include ${nginx_root}/conf.d/*.conf;
}
EOF

# Validate rendered Nginx configuration without modifying or restarting
# the operational Nginx installation.
nginx -t \
    -c "${nginx_root}/nginx.conf" \
    -p "${nginx_root}"
