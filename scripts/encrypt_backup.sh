#!/usr/bin/env bash
set -euo pipefail

# Encrypts a backup file with AES-256-CBC.
# Required env: BACKUP_ENCRYPTION_PASSWORD.
# Usage: ./scripts/encrypt_backup.sh path/to/backup.sql.gz [path/to/output.enc]

INPUT="${1:-}"
OUTPUT="${2:-${INPUT}.enc}"

if [[ -z "$INPUT" || ! -f "$INPUT" ]]; then
  echo "Usage: $0 path/to/backup.sql.gz [path/to/output.enc]" >&2
  exit 1
fi

if [[ -z "${BACKUP_ENCRYPTION_PASSWORD:-}" ]]; then
  echo "Missing BACKUP_ENCRYPTION_PASSWORD." >&2
  exit 1
fi

openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
  -in "$INPUT" \
  -out "$OUTPUT" \
  -pass env:BACKUP_ENCRYPTION_PASSWORD

echo "$OUTPUT"
