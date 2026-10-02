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

# Clean up temporary validation artifacts on exit.
# Never modify operational deployment directories.
prometheus_ci_root=""
nginx_root=""

cleanup() {
    if [[ -n "${prometheus_ci_root}" ]]; then
        rm -rf -- "${prometheus_ci_root}"
    fi

    if [[ -n "${nginx_root}" ]]; then
        rm -rf -- "${nginx_root}"
    fi
}

trap cleanup EXIT

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

# Verify all documented maintained repository paths.
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
# Never modify production configuration or access operational credentials.
if command -v promtool >/dev/null 2>&1; then
    prometheus_template="${CI_ROOT}/infra/monitoring/prometheus/prometheus.yml.example"
    prometheus_rules="${CI_ROOT}/infra/monitoring/prometheus/alerts.yml"

    prometheus_ci_root="$(
        mktemp -d "${AOS_CI_WORKDIR}/prometheus.XXXXXX"
    )"

    # Synthetic CI-only credential.
    install -m 0600 /dev/null \
        "${prometheus_ci_root}/metrics.token"

    printf '%s\n' 'aos-ci-validation-only' \
        > "${prometheus_ci_root}/metrics.token"

    # Render a temporary configuration, replacing only filesystem paths.
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

    # Full native Prometheus validation.
    promtool check config \
        "${prometheus_ci_root}/prometheus.yml"

    # Independently validate the authoritative alert rules.
    promtool check rules \
        "${prometheus_rules}"
else
    printf '%s\n' \
        'promtool unavailable; strict repository Prometheus validator completed instead.'
fi

# Native Alertmanager validation.
if command -v amtool >/dev/null 2>&1; then
    amtool check-config \
        "${CI_ROOT}/infra/monitoring/alertmanager/alertmanager.yml.example"
else
    printf '%s\n' \
        'amtool unavailable; strict repository Alertmanager validator completed instead.'
fi

# Prepare an isolated temporary Nginx configuration.
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
# These certificates are not deployment credentials.
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

# Redirect deployment filesystem references to temporary CI locations.
find "${nginx_root}/conf.d" \
    -type f -name '*.conf' \
    -exec sed -i \
        -e "s#/etc/nginx/snippets/#${nginx_root}/snippets/#g" \
        -e "s#/etc/letsencrypt/live/#${nginx_root}/certs/#g" \
        -e "s#/var/log/nginx/#${nginx_root}/logs/#g" \
        -e "s#/var/www/letsencrypt#${nginx_root}/acme#g" \
        {} +

# Convert production listeners to unprivileged loopback listeners.
#
# Nginx -t may attempt to bind configured ports. Production ports 80/443
# therefore cannot be used by an unprivileged CI process.
#
# This transformation applies ONLY to the rendered temporary configuration.
# Source-controlled production listeners remain unchanged and are separately
# checked by validate_nginx_policy.py.
#
# Preserve listener options such as ssl, default_server and ipv6only.
# Reject unexpected public listener endpoints rather than silently ignoring
# potentially unsafe changes.
"${python_executable}" - "${nginx_root}/conf.d" <<'PY'
from pathlib import Path
import re
import sys

config_dir = Path(sys.argv[1])

# Production endpoint -> temporary CI endpoint.
# IPv4 and IPv6 remain separate to avoid duplicate listener declarations
# within the same server block.
listener_map = {
    "80": "127.0.0.1:18080",
    "0.0.0.0:80": "127.0.0.1:18080",
    "*:80": "127.0.0.1:18080",
    "[::]:80": "[::1]:18080",
    "443": "127.0.0.1:18443",
    "0.0.0.0:443": "127.0.0.1:18443",
    "*:443": "127.0.0.1:18443",
    "[::]:443": "[::1]:18443",
}

# Match complete, single-line Nginx listener declarations.
# Capture the endpoint separately to preserve every existing listener option.
pattern = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"listen[ \t]+"
    r"(?P<endpoint>[^\s;]+)"
    r"(?P<options>[^;\n]*);"
    r"(?P<suffix>[ \t]*(?:#.*)?)$",
    re.MULTILINE,
)

seen_ports = {
    "80": 0,
    "443": 0,
}

files = sorted(config_dir.rglob("*.conf"))

if not files:
    raise SystemExit("No rendered Nginx configuration files were found.")

for config in files:
    original = config.read_text(encoding="utf-8")

    # Detect listener declarations that the strict parser cannot process.
    declared = re.findall(
        r"^[ \t]*listen\b.*$",
        original,
        flags=re.MULTILINE,
    )

    matches = list(pattern.finditer(original))

    if len(declared) != len(matches):
        raise SystemExit(
            f"Unsupported Nginx listener syntax in {config.name}"
        )

    def replace_listener(match):
        endpoint = match.group("endpoint")
        replacement = listener_map.get(endpoint)

        if replacement is None:
            raise SystemExit(
                f"Unexpected Nginx listener in {config.name}: {endpoint}"
            )

        port = "80" if endpoint.endswith("80") else "443"
        seen_ports[port] += 1

        return (
            f'{match.group("indent")}'
            f'listen {replacement}'
            f'{match.group("options")};'
            f'{match.group("suffix")}'
        )

    rendered = pattern.sub(replace_listener, original)

    config.write_text(rendered, encoding="utf-8")

# Both HTTP and HTTPS must exist in the canonical rendered configuration.
missing = [
    port
    for port, count in seen_ports.items()
    if count == 0
]

if missing:
    raise SystemExit(
        "Required production listeners were not found: "
        + ", ".join(missing)
    )

print(
    "Temporary Nginx listeners mapped to unprivileged "
    "IPv4/IPv6 loopback ports."
)
PY

# Generate an isolated Nginx entry configuration.
#
# No user directive is necessary: validation runs under the invoking
# unprivileged CI account.
cat > "${nginx_root}/nginx.conf" <<EOF
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

# Validate the rendered configuration using the pinned Nginx executable.
# Do not modify or restart the operational Nginx service.
nginx -t \
    -c "${nginx_root}/nginx.conf" \
    -p "${nginx_root}"

printf '%s\n' \
    'Infrastructure validation completed successfully.'
