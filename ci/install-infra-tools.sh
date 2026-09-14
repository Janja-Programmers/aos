#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

[[ $# -eq 1 ]] || die "usage: install-infra-tools.sh DESTINATION"
require_command curl
require_command git
require_command make
destination="$1"
mkdir -p "${destination}/bin" "${destination}/src"

archive="${destination}/shellcheck-v${SHELLCHECK_VERSION}.tar.xz"
curl --fail --location --retry 3 --retry-all-errors \
	"https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.xz" \
	-o "${archive}"
printf '%s  %s\n' "${SHELLCHECK_ARCHIVE_SHA256}" "${archive}" | sha256sum -c -
tar --no-same-owner -xJf "${archive}" -C "${destination}/src"
install -m 0755 "${destination}/src/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" \
	"${destination}/bin/shellcheck"

nginx_source="${destination}/src/nginx"
git init -q "${nginx_source}"
git -C "${nginx_source}" remote add origin https://github.com/nginx/nginx.git
git -C "${nginx_source}" fetch -q --depth 1 origin "refs/tags/release-${NGINX_VERSION}"
git -C "${nginx_source}" checkout -q --detach FETCH_HEAD
[[ "$(git -C "${nginx_source}" rev-parse HEAD)" == "${NGINX_REF}" ]] \
	|| die "Nginx release-${NGINX_VERSION} commit mismatch."
(
	cd "${nginx_source}"
	./auto/configure --prefix="${destination}/nginx" --with-http_ssl_module \
		--with-http_v2_module --with-pcre-jit >/dev/null
	make -j2 >/dev/null
	make install >/dev/null
)
ln -sf "${destination}/nginx/sbin/nginx" "${destination}/bin/nginx"

prometheus_archive="${destination}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz"
curl --fail --location --retry 3 --retry-all-errors \
	"https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz" \
	-o "${prometheus_archive}"
printf '%s  %s\n' "${PROMETHEUS_ARCHIVE_SHA256}" "${prometheus_archive}" | sha256sum -c -
tar --no-same-owner -xzf "${prometheus_archive}" -C "${destination}/src"
install -m 0755 "${destination}/src/prometheus-${PROMETHEUS_VERSION}.linux-amd64/promtool" \
	"${destination}/bin/promtool"

alertmanager_archive="${destination}/alertmanager-${ALERTMANAGER_VERSION}.linux-amd64.tar.gz"
curl --fail --location --retry 3 --retry-all-errors \
	"https://github.com/prometheus/alertmanager/releases/download/v${ALERTMANAGER_VERSION}/alertmanager-${ALERTMANAGER_VERSION}.linux-amd64.tar.gz" \
	-o "${alertmanager_archive}"
printf '%s  %s\n' "${ALERTMANAGER_ARCHIVE_SHA256}" "${alertmanager_archive}" | sha256sum -c -
tar --no-same-owner -xzf "${alertmanager_archive}" -C "${destination}/src"
install -m 0755 "${destination}/src/alertmanager-${ALERTMANAGER_VERSION}.linux-amd64/amtool" \
	"${destination}/bin/amtool"

"${destination}/bin/shellcheck" --version | grep -F "version: ${SHELLCHECK_VERSION}" >/dev/null
"${destination}/bin/nginx" -v 2>&1 | grep -F "nginx/${NGINX_VERSION}" >/dev/null
"${destination}/bin/promtool" --version | grep -F "version ${PROMETHEUS_VERSION}" >/dev/null
"${destination}/bin/amtool" --version | grep -F "version ${ALERTMANAGER_VERSION}" >/dev/null
printf 'Pinned ShellCheck %s, Nginx %s, Prometheus %s, and Alertmanager %s tools installed under %s\n' \
	"${SHELLCHECK_VERSION}" "${NGINX_VERSION}" "${PROMETHEUS_VERSION}" \
	"${ALERTMANAGER_VERSION}" "${destination}"
