#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <env-file> <fixture-path> [--flush]"
  exit 1
fi

ENV_FILE="$1"
FIXTURE_PATH="$2"
FLUSH_FIRST="${3:-}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE"
  exit 1
fi

if [[ ! -f "$FIXTURE_PATH" ]]; then
  echo "Fixture file not found: $FIXTURE_PATH"
  exit 1
fi

cd "$APP_DIR"
set -a
source "$ENV_FILE"
set +a

echo "[1/5] Running Django check"
"$PYTHON_BIN" manage.py check

if [[ "$FLUSH_FIRST" == "--flush" ]]; then
  echo "[2/5] Flushing target database"
  "$PYTHON_BIN" manage.py flush --noinput
else
  echo "[2/5] Skipping flush"
fi

echo "[3/5] Applying fixture $FIXTURE_PATH"
"$PYTHON_BIN" manage.py loaddata "$FIXTURE_PATH"

echo "[4/5] Running Django check again"
"$PYTHON_BIN" manage.py check

echo "[5/5] Import completed"
