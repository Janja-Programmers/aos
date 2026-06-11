#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR="${1:-}"
[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || { echo "Usage: $0 /path/to/backup" >&2; exit 2; }

required=(metadata.env SHA256SUMS frappe)
for item in "${required[@]}"; do
  [[ -e "$BACKUP_DIR/$item" ]] || { echo "Missing required backup item: $item" >&2; exit 1; }
done

compgen -G "$BACKUP_DIR/frappe/*" >/dev/null || { echo "Frappe backup directory is empty." >&2; exit 1; }

(
  cd "$BACKUP_DIR"
  sha256sum --check --strict SHA256SUMS
)

# Validate gzip/tar archives without extracting them.
while IFS= read -r -d '' archive; do
  tar -tzf "$archive" >/dev/null
 done < <(find "$BACKUP_DIR" -type f -name '*.tar.gz' -print0)

echo "Backup verified successfully: $BACKUP_DIR"
