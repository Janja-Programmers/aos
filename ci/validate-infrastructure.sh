#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
require_command shellcheck
require_command nginx
python_executable="$(python314)"

mapfile -t scripts < <(find "${CI_ROOT}/infra" -type f -name '*.sh' -print | sort)
[[ "${#scripts[@]}" -gt 0 ]] || die "No maintained infrastructure shell scripts were found."
for script in "${scripts[@]}"; do
	bash -n "${script}"
	if [[ "${script}" != *"/env.example.sh" && ! -x "${script}" ]]; then
		die "Maintained script is not executable: ${script#${CI_ROOT}/}"
	fi
done
shellcheck --severity=warning "${scripts[@]}"

while IFS= read -r relative; do
	[[ -e "${CI_ROOT}/${relative}" ]] || die "Documented maintained path is missing: ${relative}"
done <"${CI_ROOT}/ci/maintained-paths.txt"

"${python_executable}" "${CI_ROOT}/ci/validate_systemd.py"
"${python_executable}" "${CI_ROOT}/ci/validate_doc_paths.py"

nginx_root="$(mktemp -d "${AOS_CI_WORKDIR}/nginx.XXXXXX")"
nginx_mime_types="${NGINX_MIME_TYPES:-/etc/nginx/mime.types}"
[[ -f "${nginx_mime_types}" ]] || die "Nginx mime.types is missing: ${nginx_mime_types}"
"${python_executable}" "${CI_ROOT}/ci/render_nginx.py" "${nginx_root}"
mkdir -p "${nginx_root}/logs" "${nginx_root}/acme"
for domain in api.invalid live.invalid maps.invalid files.invalid; do
	mkdir -p "${nginx_root}/certs/${domain}"
	openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=${domain}" \
		-keyout "${nginx_root}/certs/${domain}/privkey.pem" \
		-out "${nginx_root}/certs/${domain}/fullchain.pem" >/dev/null 2>&1
done

find "${nginx_root}/conf.d" -type f -name '*.conf' -exec sed -i \
	-e "s#/etc/nginx/snippets/#${nginx_root}/snippets/#g" \
	-e "s#/etc/letsencrypt/live/#${nginx_root}/certs/#g" \
	-e "s#/var/log/nginx/#${nginx_root}/logs/#g" \
	-e "s#/var/www/letsencrypt#${nginx_root}/acme#g" {} +

cat >"${nginx_root}/nginx.conf" <<EOF
user $(id -un);
error_log ${nginx_root}/logs/error.log;
pid ${nginx_root}/nginx.pid;
events { worker_connections 64; }
http {
    map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }
    include ${nginx_mime_types};
    include ${nginx_root}/conf.d/*.conf;
}
EOF
nginx -t -c "${nginx_root}/nginx.conf" -p "${nginx_root}"
