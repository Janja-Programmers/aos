#!/usr/bin/env bash
set -euo pipefail

VIDEO_URL="${VIDEO_SERVICE_URL:-http://127.0.0.1:8130}"
MODERATION_URL="${MODERATION_SERVICE_URL:-http://127.0.0.1:8140}"
SEARCH_URL="${SEARCH_RANKING_SERVICE_URL:-http://127.0.0.1:8150}"
NOTIFICATION_URL="${NOTIFICATION_SERVICE_URL:-http://127.0.0.1:8160}"
ANALYTICS_URL="${ANALYTICS_SERVICE_URL:-http://127.0.0.1:8170}"

check_endpoint() {
  local label="$1"
  local url="$2"
  local path="$3"
  printf '%-28s %s\n' "${label} ${path}" "${url}${path}"
  curl --fail --silent --show-error "${url}${path}"
  printf '\n'
}

check_endpoint "video-processing" "${VIDEO_URL}" "/health"
check_endpoint "video-processing" "${VIDEO_URL}" "/ready"
check_endpoint "content-moderation" "${MODERATION_URL}" "/health"
check_endpoint "content-moderation" "${MODERATION_URL}" "/ready"
check_endpoint "search-ranking" "${SEARCH_URL}" "/health"
check_endpoint "search-ranking" "${SEARCH_URL}" "/ready"
check_endpoint "notification-delivery" "${NOTIFICATION_URL}" "/health"
check_endpoint "notification-delivery" "${NOTIFICATION_URL}" "/ready"
check_endpoint "analytics-pipeline" "${ANALYTICS_URL}" "/health"
check_endpoint "analytics-pipeline" "${ANALYTICS_URL}" "/ready"

printf '\nAll AOS external service health checks passed.\n'
