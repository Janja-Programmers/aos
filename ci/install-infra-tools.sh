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
"${destination}/bin/shellcheck" --version | grep -F "version: ${SHELLCHECK_VERSION}" >/dev/null
"${destination}/bin/nginx" -v 2>&1 | grep -F "nginx/${NGINX_VERSION}" >/dev/null
printf 'Pinned ShellCheck %s and Nginx %s tools installed under %s\n' \
	"${SHELLCHECK_VERSION}" "${NGINX_VERSION}" "${destination}"
