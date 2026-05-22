#!/usr/bin/env bash
set -euo pipefail

# Creates a compressed MariaDB/MySQL backup using environment variables only.
# Required env: DB_NAME, DB_USER, DB_PASSWORD.
# Optional env: DB_HOST, DB_PORT, BACKUP_DIR.

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUTPUT="${BACKUP_DIR}/${DB_NAME:-edugradeplus}-${TIMESTAMP}.sql.gz"

if [[ -z "${DB_NAME:-}" || -z "${DB_USER:-}" || -z "${DB_PASSWORD:-}" ]]; then
  echo "Missing DB_NAME, DB_USER, or DB_PASSWORD." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

MYSQL_PWD="$DB_PASSWORD" mysqldump \
  --host="$DB_HOST" \
  --port="$DB_PORT" \
  --user="$DB_USER" \
  --single-transaction \
  --quick \
  --routines \
  --triggers \
  "$DB_NAME" | gzip -9 > "$OUTPUT"

echo "$OUTPUT"
