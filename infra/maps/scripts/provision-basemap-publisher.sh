#!/usr/bin/env bash
set -euo pipefail

required_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf '%s is required\n' "$name" >&2
    exit 2
  fi
}

for name in \
  MAPS_OBJECT_STORAGE_ENDPOINT \
  MAPS_OBJECT_STORAGE_BUCKET \
  MAPS_OBJECT_STORAGE_ACCESS_KEY \
  MAPS_OBJECT_STORAGE_SECRET_KEY \
  MAPS_OBJECT_STORAGE_POLICY_ACCESS_KEY \
  MAPS_OBJECT_STORAGE_POLICY_SECRET_KEY; do
  required_env "$name"
done

MC_BIN="${MAPS_MINIO_MC_BIN:-mc}"
if ! command -v "$MC_BIN" >/dev/null 2>&1; then
  echo "MinIO client 'mc' is required. Install a pinned MinIO Client release or set MAPS_MINIO_MC_BIN." >&2
  exit 3
fi

CONFIG_DIR="$(mktemp -d)"
POLICY_FILE="$(mktemp)"
trap 'rm -rf "$CONFIG_DIR" "$POLICY_FILE"' EXIT
export MC_CONFIG_DIR="$CONFIG_DIR"

ALIAS="aos-maps-admin"
POLICY_NAME="aos-maps-basemap-publisher"
BUCKET="$MAPS_OBJECT_STORAGE_BUCKET"

"$MC_BIN" alias set \
  "$ALIAS" \
  "$MAPS_OBJECT_STORAGE_ENDPOINT" \
  "$MAPS_OBJECT_STORAGE_POLICY_ACCESS_KEY" \
  "$MAPS_OBJECT_STORAGE_POLICY_SECRET_KEY" \
  --api S3v4 >/dev/null

cat > "$POLICY_FILE" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads"
      ],
      "Resource": ["arn:aws:s3:::$BUCKET"]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": ["arn:aws:s3:::$BUCKET/basemap/*"]
    }
  ]
}
JSON

"$MC_BIN" admin policy create "$ALIAS" "$POLICY_NAME" "$POLICY_FILE" >/dev/null
# Re-running user add intentionally rotates the secret to the configured value.
"$MC_BIN" admin user add \
  "$ALIAS" \
  "$MAPS_OBJECT_STORAGE_ACCESS_KEY" \
  "$MAPS_OBJECT_STORAGE_SECRET_KEY" >/dev/null
"$MC_BIN" admin policy attach \
  "$ALIAS" \
  "$POLICY_NAME" \
  --user "$MAPS_OBJECT_STORAGE_ACCESS_KEY" >/dev/null

"$MC_BIN" admin user info "$ALIAS" "$MAPS_OBJECT_STORAGE_ACCESS_KEY" >/dev/null
printf '{"bucket":"%s","policy":"%s","publisher":"provisioned"}\n' "$BUCKET" "$POLICY_NAME"
