#!/usr/bin/env bash
# Deploy VirusRoulette to VPS: code, .env, sessions, venv, nginx, TLS, systemd.
# Usage:
#   ./deploy.sh
#   CERTBOT_EMAIL=you@example.com ./deploy.sh
#
# Optional env:
#   DEPLOY_USER (default: Th3ryks)
#   DEPLOY_HOST (default: 34.51.239.95)
#   DEPLOY_DOMAIN (default: vroulette.th3ryks.dev)
#   DEPLOY_REMOTE_DIR (default: /home/Th3ryks/VirusRoulette)

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-Th3ryks}"
DEPLOY_HOST="${DEPLOY_HOST:-34.51.239.95}"
DEPLOY_DOMAIN="${DEPLOY_DOMAIN:-vroulette.th3ryks.dev}"
DEPLOY_REMOTE_DIR="${DEPLOY_REMOTE_DIR:-/home/${DEPLOY_USER}/VirusRoulette}"
REMOTE="${DEPLOY_USER}@${DEPLOY_HOST}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RSYNC_EXCLUDES=(
  --exclude '.git'
  --exclude '.venv'
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.DS_Store'
  --exclude 'account*.session'
  --exclude 'account*.session-journal'
  --exclude '*.personal.session.bak'
)

echo ">>> Syncing project to ${REMOTE}:${DEPLOY_REMOTE_DIR}/"
ssh -o BatchMode=yes "${REMOTE}" "mkdir -p '${DEPLOY_REMOTE_DIR}/sessions' '${DEPLOY_REMOTE_DIR}/dashboard'"
rsync -avz "${RSYNC_EXCLUDES[@]}" \
  --include '.env' \
  "${SCRIPT_DIR}/" "${REMOTE}:${DEPLOY_REMOTE_DIR}/"

echo ">>> Syncing sessions/"
rsync -avz "${SCRIPT_DIR}/sessions/" "${REMOTE}:${DEPLOY_REMOTE_DIR}/sessions/"

echo ">>> Configuring server (venv, nginx, certbot, systemd)..."
# shellcheck disable=SC2087
ssh -o BatchMode=yes "${REMOTE}" bash -s <<REMOTE_SETUP
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER}"
DEPLOY_DOMAIN="${DEPLOY_DOMAIN}"
DEPLOY_REMOTE_DIR="${DEPLOY_REMOTE_DIR}"
CERTBOT_EMAIL="${CERTBOT_EMAIL}"

export DEBIAN_FRONTEND=noninteractive
sudo apt-get -o DPkg::Lock::Timeout=120 update -y
sudo apt-get -o DPkg::Lock::Timeout=120 install -y \
  nginx certbot python3-certbot-nginx python3-venv python3-pip \
  build-essential python3-dev libssl-dev

chmod 700 "\${DEPLOY_REMOTE_DIR}"
chmod 600 "\${DEPLOY_REMOTE_DIR}/.env" 2>/dev/null || true
chmod 700 "\${DEPLOY_REMOTE_DIR}/sessions"
chmod 600 "\${DEPLOY_REMOTE_DIR}/sessions/"*.session 2>/dev/null || true

if [[ ! -d "\${DEPLOY_REMOTE_DIR}/.venv" ]]; then
  python3 -m venv "\${DEPLOY_REMOTE_DIR}/.venv"
fi
"\${DEPLOY_REMOTE_DIR}/.venv/bin/pip" install -U pip wheel
set +e
"\${DEPLOY_REMOTE_DIR}/.venv/bin/pip" install -r "\${DEPLOY_REMOTE_DIR}/requirements.txt"
PIP_RC=\$?
set -e
if [[ \$PIP_RC -ne 0 ]]; then
  echo "Full requirements failed (often TgCrypto on new Python); installing without TgCrypto..."
  "\${DEPLOY_REMOTE_DIR}/.venv/bin/pip" install aiohttp aiogram kurigram python-dotenv loguru 'qrcode[pil]'
fi

sudo tee /etc/nginx/sites-available/\${DEPLOY_DOMAIN} >/dev/null <<NGINX_EOF
server {
    listen 80;
    listen [::]:80;
    server_name \${DEPLOY_DOMAIN};

    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX_EOF

sudo ln -sf /etc/nginx/sites-available/\${DEPLOY_DOMAIN} /etc/nginx/sites-enabled/\${DEPLOY_DOMAIN}
sudo nginx -t
sudo systemctl reload nginx

set +e
if [[ -n "\${CERTBOT_EMAIL}" ]]; then
  sudo certbot --nginx -d "\${DEPLOY_DOMAIN}" --non-interactive --agree-tos -m "\${CERTBOT_EMAIL}" --redirect
else
  sudo certbot --nginx -d "\${DEPLOY_DOMAIN}" --non-interactive --agree-tos --register-unsafely-without-email --redirect
fi
CERT_RC=\$?
set -e
if [[ \$CERT_RC -eq 0 ]]; then
  echo "TLS OK for \${DEPLOY_DOMAIN}"
else
  echo "WARNING: certbot failed (DNS A/AAAA for \${DEPLOY_DOMAIN} must point here / through CF). Re-run later."
fi

sudo tee /etc/systemd/system/virusroulette.service >/dev/null <<UNIT_EOF
[Unit]
Description=VirusRoulette bot + dashboard
After=network.target

[Service]
Type=simple
User=\${DEPLOY_USER}
Group=\${DEPLOY_USER}
WorkingDirectory=\${DEPLOY_REMOTE_DIR}
Environment=PATH=\${DEPLOY_REMOTE_DIR}/.venv/bin:/usr/bin
EnvironmentFile=\${DEPLOY_REMOTE_DIR}/.env
ExecStart=\${DEPLOY_REMOTE_DIR}/.venv/bin/python \${DEPLOY_REMOTE_DIR}/main.py
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT_EOF

sudo systemctl daemon-reload
sudo systemctl enable virusroulette.service
sudo systemctl restart virusroulette.service
sleep 4
sudo systemctl --no-pager -l status virusroulette.service || true
echo "---- recent logs ----"
sudo journalctl -u virusroulette -n 40 --no-pager || true
REMOTE_SETUP

echo ">>> Done."
echo "    Dashboard: https://${DEPLOY_DOMAIN}/"
echo "    Status: ssh ${REMOTE} 'sudo systemctl status virusroulette'"
echo "    Logs:   ssh ${REMOTE} 'sudo journalctl -u virusroulette -f'"
