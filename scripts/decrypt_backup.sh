#!/usr/bin/env bash
set -euo pipefail

# Decrypts a backup file encrypted by encrypt_backup.sh.
# Required env: BACKUP_ENCRYPTION_PASSWORD.
# Usage: ./scripts/decrypt_backup.sh path/to/backup.sql.gz.enc [path/to/output.sql.gz]

INPUT="${1:-}"
OUTPUT="${2:-${INPUT%.enc}}"

if [[ -z "$INPUT" || ! -f "$INPUT" ]]; then
  echo "Usage: $0 path/to/backup.sql.gz.enc [path/to/output.sql.gz]" >&2
  exit 1
fi

if [[ -z "${BACKUP_ENCRYPTION_PASSWORD:-}" ]]; then
  echo "Missing BACKUP_ENCRYPTION_PASSWORD." >&2
  exit 1
fi

openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter 200000 \
  -in "$INPUT" \
  -out "$OUTPUT" \
  -pass env:BACKUP_ENCRYPTION_PASSWORD

echo "$OUTPUT"
