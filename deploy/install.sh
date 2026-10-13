#!/usr/bin/env bash
# Installation de Kairos dans un conteneur LXC Debian (Proxmox).
# À lancer depuis la racine du projet, en root :
#   bash deploy/install.sh
#
# Variables surchargeables : APP_DIR, SERVICE_USER
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/kairos}"
SERVICE_USER="${SERVICE_USER:-kairos}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "À lancer en root (dans le conteneur)." >&2
  exit 1
fi

cd "$APP_DIR"

echo "==> Dépendances système"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip sqlite3

echo "==> Environnement Python"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Utilisateur de service"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin "$SERVICE_USER"

echo "==> Fichier .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "    .env créé depuis le modèle — PENSE À Y METTRE LE TOKEN."
fi
chmod 600 .env

echo "==> Dossiers de données"
mkdir -p data backups
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "==> Unit systemd"
sed -e "s#/opt/kairos#${APP_DIR}#g" -e "s#User=kairos#User=${SERVICE_USER}#" \
  deploy/kairos.service > /etc/systemd/system/kairos.service
systemctl daemon-reload
systemctl enable kairos >/dev/null

echo "==> Sauvegarde quotidienne (cron)"
cat > /etc/cron.d/kairos-backup <<EOF
# Sauvegarde quotidienne de la base Kairos à 04:00
0 4 * * * ${SERVICE_USER} APP_DIR=${APP_DIR} ${APP_DIR}/deploy/backup.sh >/dev/null 2>&1
EOF
chmod 644 /etc/cron.d/kairos-backup
chmod +x deploy/backup.sh 2>/dev/null || true

cat <<EOF

✅ Installation terminée.

Étapes suivantes :
  1. nano ${APP_DIR}/.env        # colle KAIROS_TELEGRAM_BOT_TOKEN (et le CHAT_ID)
  2. systemctl start kairos
  3. journalctl -u kairos -f     # vérifie « Bot Telegram démarré »
EOF
