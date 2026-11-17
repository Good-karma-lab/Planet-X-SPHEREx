#!/usr/bin/env bash
# Release from a development machine to the VM: build the web app here (it is the same on every architecture), copy the code,
# sync the Python environment there and restart the API. The store on the VM (index, products) stays.
#   deploy/push.sh ubuntu@HOST [--index] [--now]   --index also copies a consistent snapshot of the local exposure index;
#                                                  without --now the main instance restarts only when no job is running (up to 20 min)
#   deploy/push.sh ubuntu@HOST --web-only            only the web bundle (the API serves it from disk): no restart, running jobs go on
set -euo pipefail
HOST="${1:?usage: deploy/push.sh user@host [--index]}"
KEY="${SKY_SSH_KEY:-$HOME/.ssh/oci_spherex_ed25519}"
SSH="ssh -i $KEY -o ConnectTimeout=20"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

(cd web && npm run build >/dev/null)
rev="$(git rev-parse --short HEAD)$(git diff --quiet || echo '-dirty')"
echo "$rev" > web/dist/release.txt

# the hashed assets of earlier builds stay (a tab opened before this release still loads its lazy parts); everything else is replaced
keep_assets() { rsync -az -e "$SSH" --rsync-path="sudo -u sky rsync" web/dist/assets/ "$HOST:/opt/sky/web/dist/assets/"; }
if [ "${2:-}" = "--web-only" ]; then
  keep_assets
  rsync -az --delete --exclude assets -e "$SSH" --rsync-path="sudo -u sky rsync" web/dist/ "$HOST:/opt/sky/web/dist/"
  echo "released web $rev to $HOST (no restart)"
  exit 0
fi

rsync -az --delete -e "$SSH" --rsync-path="sudo -u sky rsync" \
  --exclude .git --exclude .venv --exclude store --exclude 'web/node_modules' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' --exclude 'web/test-results' --exclude 'web/playwright-report' --exclude '.claude' --exclude 'web/dist/assets' \
  ./ "$HOST:/opt/sky/"
keep_assets

INDEX=0; NOW=0
for a in "${@:2}"; do [ "$a" = "--index" ] && INDEX=1; [ "$a" = "--now" ] && NOW=1; done
if [ "$INDEX" = 1 ]; then
  snap="$(mktemp -d)/exposures.sqlite"
  sqlite3 store/index/exposures.sqlite ".backup '$snap'"
  rsync -az --partial --progress -e "$SSH" --rsync-path="sudo -u sky rsync" "$snap" "$HOST:/var/lib/sky/index/exposures.sqlite"
  rm -f "$snap"
fi

# zero-downtime: the standby restarts first and must answer; then the main instance restarts while the standby serves
$SSH "$HOST" 'set -e
sudo install -m 644 /opt/sky/deploy/systemd/sky-api.service /opt/sky/deploy/systemd/sky-api-standby.service /opt/sky/deploy/systemd/sky-index.service /opt/sky/deploy/systemd/sky-index.timer /opt/sky/deploy/systemd/sky-disk.service /opt/sky/deploy/systemd/sky-disk.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo -u sky bash -lc "cd /opt/sky && ~/.local/bin/uv sync --frozen --no-dev -q"
healthy() { for i in $(seq 1 90); do curl -sf -m 3 "http://127.0.0.1:$1/api/v1/health" >/dev/null && return 0; sleep 1; done; echo "port $1 did not come up" >&2; return 1; }
sudo systemctl enable --now sky-api-standby.service
sudo systemctl restart sky-api-standby.service && healthy 8781
if [ "'"$NOW"'" != 1 ]; then
  for i in $(seq 1 240); do
    running=$(curl -sf -m 5 http://127.0.0.1:8780/api/v1/health | python3 -c "import json,sys; print(json.load(sys.stdin)[\"jobs\"][\"running\"])" 2>/dev/null || echo 0)
    [ "$running" = "0" ] && break
    [ "$i" = 1 ] && echo "waiting until no job is running (a half-built field would be lost)…"
    sleep 5
  done
fi
sudo systemctl restart sky-api.service && healthy 8780
curl -s -m 10 http://127.0.0.1:8780/api/v1/health | head -c 160; echo'
echo "released $rev to $HOST"
