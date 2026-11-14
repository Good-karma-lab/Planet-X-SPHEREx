#!/usr/bin/env bash
# Provision an Ubuntu 24.04 (aarch64 or x86-64) VM for SPHEREx Sky. Idempotent; run as root.
set -euo pipefail

REPO="${SKY_REPO:-https://github.com/Good-karma-lab/Planet-X-SPHEREx.git}"
APP=/opt/sky
STORE=/var/lib/sky

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates rclone sqlite3 ufw unattended-upgrades

# firewall: SSH only (the Tunnel is outbound)
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

id sky >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/sky --shell /bin/bash sky
mkdir -p "$STORE" /etc/sky
chown sky:sky "$STORE"
[ -f /etc/sky/env ] || { echo "SKY_WORKERS=2" > /etc/sky/env; chmod 600 /etc/sky/env; }

# Node 22 for building the web app
if ! command -v node >/dev/null || [ "$(node -v | cut -c2-3)" -lt 22 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

# uv for the sky user
sudo -u sky bash -c 'command -v ~/.local/bin/uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'

if [ ! -d "$APP/.git" ]; then
  git clone "$REPO" "$APP"
  chown -R sky:sky "$APP"
fi
sudo -u sky "$APP/deploy/release.sh" --no-restart

# cloudflared
if ! command -v cloudflared >/dev/null; then
  arch=$(dpkg --print-architecture)
  curl -fsSL -o /tmp/cloudflared.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  dpkg -i /tmp/cloudflared.deb
fi

install -m 644 "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sky-api.service sky-index.timer sky-snapshot.timer
if grep -q '^CLOUDFLARE_TUNNEL_TOKEN=' /etc/sky/env; then
  systemctl enable --now sky-tunnel.service
fi
echo "provisioned; health: curl -s http://127.0.0.1:8780/api/v1/health"
