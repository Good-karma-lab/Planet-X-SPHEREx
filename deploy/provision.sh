#!/usr/bin/env bash
# Provision an Ubuntu 24.04 VM (x86-64 or aarch64) for SPHEREx Sky. Idempotent; run as root on the VM:
#   sudo SKY_DOMAIN=spherex.space bash /tmp/provision.sh
# The code arrives by deploy/push.sh from a development machine (or by git once the repository is public).
set -euo pipefail

APP=/opt/sky
STORE=/var/lib/sky
DOMAIN="${SKY_DOMAIN:-}"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates rsync sqlite3 rclone unattended-upgrades \
  debian-keyring debian-archive-keyring apt-transport-https gnupg

# Caddy (official repository): HTTPS with automatic certificates in front of the API
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# Firewall: the Ubuntu images of Oracle Cloud keep their rules in iptables (/etc/iptables/rules.v4); 22, 80 and 443 must be open there
# and in the VCN security list. ufw is not used, so the two never disagree.
for port in 80 443; do
  iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
command -v netfilter-persistent >/dev/null && netfilter-persistent save || true

id sky >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/sky --shell /bin/bash sky
mkdir -p "$APP" "$STORE/index" /etc/sky
chown sky:sky "$APP" "$STORE" "$STORE/index"
[ -f /etc/sky/env ] || { printf 'SKY_WORKERS=3\n' > /etc/sky/env; chmod 600 /etc/sky/env; }

# uv for the sky user (it installs the pinned Python)
sudo -u sky bash -c 'test -x ~/.local/bin/uv || curl -LsSf https://astral.sh/uv/install.sh | sh'

# the sky user may restart its own service and nothing else
cat > /etc/sudoers.d/sky <<'SUDO'
sky ALL=(root) NOPASSWD: /usr/bin/systemctl restart sky-api.service, /usr/bin/systemctl restart sky-api-standby.service, /usr/bin/systemctl reload caddy
SUDO
chmod 440 /etc/sudoers.d/sky

# Caddyfile: the domain with automatic HTTPS, and the bare address over HTTP
# the main instance takes the traffic; while it restarts the standby (8781) answers, and a request waits up to 30 s for either
UPSTREAM='\treverse_proxy 127.0.0.1:8780 127.0.0.1:8781 {\n\t\tlb_policy first\n\t\tlb_try_duration 30s\n\t\tlb_try_interval 250ms\n\t\thealth_uri /api/v1/health\n\t\thealth_interval 2s\n\t\thealth_timeout 2s\n\t\tfail_duration 5s\n\t}\n'
{
  if [ -n "$DOMAIN" ]; then
    printf '%s, www.%s {\n\tencode {\n\t\tzstd\n\t\tgzip\n\t\tmatch {\n\t\t\theader Content-Type text/*\n\t\t\theader Content-Type application/json*\n\t\t\theader Content-Type application/javascript*\n\t\t\theader Content-Type application/octet-stream*\n\t\t\theader Content-Type image/svg+xml*\n\t\t}\n\t}\n'"$UPSTREAM"'}\n\n' "$DOMAIN" "$DOMAIN"
  fi
  printf ':80 {\n\tencode zstd gzip\n'"$UPSTREAM"'}\n'
} > /etc/caddy/Caddyfile.new
# a Caddyfile that does not validate never replaces the running one
if caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile.new; then
  mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
else
  echo "the new Caddyfile does not validate; the old one stays" >&2
fi
systemctl enable caddy
systemctl reload caddy || systemctl restart caddy

if [ -d "$APP/deploy/systemd" ]; then
  install -m 644 "$APP"/deploy/systemd/sky-api.service "$APP"/deploy/systemd/sky-api-standby.service "$APP"/deploy/systemd/sky-index.service "$APP"/deploy/systemd/sky-index.timer /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable sky-api.service sky-api-standby.service sky-index.timer
fi
echo "provisioned; after the first push: systemctl start sky-api; curl -s http://127.0.0.1:8780/api/v1/health"
