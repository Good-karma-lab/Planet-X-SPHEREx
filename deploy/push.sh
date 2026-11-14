#!/usr/bin/env bash
# Release from a development machine to the VM: build the web app here (it is the same on every architecture), copy the code,
# sync the Python environment there and restart the API. The store on the VM (index, products) stays.
#   deploy/push.sh ubuntu@HOST [--index]      --index also copies a consistent snapshot of the local exposure index
set -euo pipefail
HOST="${1:?usage: deploy/push.sh user@host [--index]}"
KEY="${SKY_SSH_KEY:-$HOME/.ssh/oci_spherex_ed25519}"
SSH="ssh -i $KEY -o ConnectTimeout=20"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

(cd web && npm run build >/dev/null)
rev="$(git rev-parse --short HEAD)$(git diff --quiet || echo '-dirty')"
echo "$rev" > web/dist/release.txt

rsync -az --delete -e "$SSH" --rsync-path="sudo -u sky rsync" \
  --exclude .git --exclude .venv --exclude store --exclude 'web/node_modules' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' --exclude 'web/test-results' --exclude 'web/playwright-report' --exclude '.claude' \
  ./ "$HOST:/opt/sky/"

if [ "${2:-}" = "--index" ]; then
  snap="$(mktemp -d)/exposures.sqlite"
  sqlite3 store/index/exposures.sqlite ".backup '$snap'"
  rsync -az --partial --progress -e "$SSH" --rsync-path="sudo -u sky rsync" "$snap" "$HOST:/var/lib/sky/index/exposures.sqlite"
  rm -f "$snap"
fi

$SSH "$HOST" 'sudo install -m 644 /opt/sky/deploy/systemd/sky-api.service /opt/sky/deploy/systemd/sky-index.service /opt/sky/deploy/systemd/sky-index.timer /etc/systemd/system/ \
  && sudo systemctl daemon-reload \
  && sudo -u sky bash -lc "cd /opt/sky && ~/.local/bin/uv sync --frozen --no-dev -q" \
  && sudo systemctl restart sky-api.service && sleep 3 && curl -s -m 10 http://127.0.0.1:8780/api/v1/health | head -c 160; echo'
echo "released $rev to $HOST"
