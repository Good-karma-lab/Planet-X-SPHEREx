#!/usr/bin/env bash
# Update the app in place (run as the sky user). --no-restart: only build.
set -euo pipefail
cd /opt/sky
git pull --ff-only
~/.local/bin/uv sync --frozen --no-dev
(cd web && npm ci && npm run build)
if [ "${1:-}" != "--no-restart" ]; then
  sudo systemctl restart sky-api.service
fi
