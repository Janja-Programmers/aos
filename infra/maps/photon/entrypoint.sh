#!/usr/bin/env sh
set -eu

PHOTON_HOME="${PHOTON_HOME:-/opt/photon}"
PHOTON_LISTEN_IP="${PHOTON_LISTEN_IP:-0.0.0.0}"
PHOTON_PORT="${PHOTON_PORT:-2322}"
PHOTON_MAX_RESULTS="${PHOTON_MAX_RESULTS:-20}"
PHOTON_MAX_REVERSE_RESULTS="${PHOTON_MAX_REVERSE_RESULTS:-10}"
PHOTON_QUERY_TIMEOUT_SECONDS="${PHOTON_QUERY_TIMEOUT_SECONDS:-5}"
PHOTON_DEFAULT_LANGUAGE="${PHOTON_DEFAULT_LANGUAGE:-en}"
PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES="${PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES:-}"
PHOTON_OPENSEARCH_CLUSTER="${PHOTON_OPENSEARCH_CLUSTER:-photon}"
PHOTON_METRICS_ENABLED="${PHOTON_METRICS_ENABLED:-true}"
JAVA_OPTS="${JAVA_OPTS:--Xms512m -Xmx2g -XX:+ExitOnOutOfMemoryError}"

[ -s "${PHOTON_HOME}/photon.jar" ] || { echo "Photon jar missing" >&2; exit 1; }

common_args=""
if [ -n "${PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES}" ]; then
    common_args="-transport-addresses ${PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES} -cluster ${PHOTON_OPENSEARCH_CLUSTER}"
fi

if [ "$#" -eq 0 ]; then set -- serve; fi
if [ "$1" = "serve" ]; then
    shift
    metrics_arg=""
    if [ "${PHOTON_METRICS_ENABLED}" = "true" ]; then metrics_arg="-metrics-enable prometheus"; fi
    # shellcheck disable=SC2086
    exec java ${JAVA_OPTS} -jar "${PHOTON_HOME}/photon.jar" serve \
        ${common_args} \
        -listen-ip "${PHOTON_LISTEN_IP}" \
        -listen-port "${PHOTON_PORT}" \
        -max-results "${PHOTON_MAX_RESULTS}" \
        -max-reverse-results "${PHOTON_MAX_REVERSE_RESULTS}" \
        -query-timeout "${PHOTON_QUERY_TIMEOUT_SECONDS}" \
        -default-language "${PHOTON_DEFAULT_LANGUAGE}" \
        ${metrics_arg} "$@"
fi

# shellcheck disable=SC2086
exec java ${JAVA_OPTS} -jar "${PHOTON_HOME}/photon.jar" "$@" ${common_args}
