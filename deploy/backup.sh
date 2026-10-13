#!/usr/bin/env bash
# Sauvegarde de la base Kairos avec rotation (via l'API sqlite3 de Python).
#   APP_DIR=/opt/kairos KEEP=60 bash deploy/backup.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/kairos}"
KEEP="${KEEP:-60}"

exec "${APP_DIR}/.venv/bin/python" "${APP_DIR}/tools/backup_db.py" \
  --db "${APP_DIR}/data/kairos.db" \
  --dest "${APP_DIR}/backups" \
  --keep "${KEEP}"
