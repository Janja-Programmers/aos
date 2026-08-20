# shellcheck shell=bash
# Copy this to env.local.sh, replace values, then: source infra/load-testing/k6/env.local.sh
# Never commit real passwords, admin credentials, or production secrets.

export BASE_URL="https://aos-staging.duckdns.org"

# Enabled staging load-test users. Use non-admin users for normal flows.
export USER_EMAIL="load-user@example.com"
export USER_PASSWORD="replace-with-staging-test-password"
export SECOND_USER_EMAIL="load-user-2@example.com"
export SECOND_USER_PASSWORD="replace-with-staging-test-password"

# Optional account with Read permission on AOS Settings for diagnostics.js only.
export ADMIN_EMAIL="admin-load-check@example.com"
export ADMIN_PASSWORD="replace-with-staging-admin-password"

# Seed IDs from staging data. Comma-separated values are allowed where noted.
export AD_IDS="AD-2026-00001,AD-2026-00002"
export TEST_SHORT_ID="SHORT-2026-00001"
export SHORT_IDS="SHORT-2026-00001,SHORT-2026-00002"
export CHAT_CONVERSATION_ID="CONV-2026-00001"
export CHAT_RECEIVER_USER="load-user-2@example.com"
export LIVE_ID="LIVE-2026-00001"

# Safe defaults: write-heavy flows are disabled until explicitly enabled.
export RUN_WRITES="false"
export RUN_MEDIA_INIT="false"
export RUN_SHORT_WRITES="false"
export RUN_CHAT_WRITES="false"
export RUN_LIVE_START="false"
export RUN_LIVE_JOIN="false"
export RUN_NOTIFICATION_WRITES="false"
export RUN_ADMIN_DIAGNOSTICS="false"

export VUS="5"
export DURATION="2m"
export REQUEST_TIMEOUT="10s"
