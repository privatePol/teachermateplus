#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/edugradeplus}"
VENV_BIN="${VENV_BIN:-/opt/edugradeplus/.venv/bin}"
ENV_FILE="${ENV_FILE:-/etc/edugradeplus/edugradeplus.env}"
SERVICE_NAME="${SERVICE_NAME:-edugradeplus-gunicorn}"
BRANCH="${BRANCH:-main}"

echo "[1/7] Loading environment from ${ENV_FILE}"
set -a
source "${ENV_FILE}"
set +a

echo "[2/7] Pulling latest code (${BRANCH})"
cd "${APP_DIR}"
git fetch origin
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

echo "[3/7] Installing dependencies"
"${VENV_BIN}/pip" install --upgrade pip
"${VENV_BIN}/pip" install -r requirements/production.txt

echo "[4/7] Applying migrations"
"${VENV_BIN}/python" manage.py migrate --noinput

echo "[5/7] Collecting static files"
"${VENV_BIN}/python" manage.py collectstatic --noinput

echo "[6/7] Running Django checks"
"${VENV_BIN}/python" manage.py check

echo "[7/7] Restarting ${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl status "${SERVICE_NAME}" --no-pager

echo "Deployment completed successfully."
