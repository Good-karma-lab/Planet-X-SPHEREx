#!/usr/bin/env bash
# The flow e2e (web/tests/e2e/flow.e2e.ts) on the second node, not on a laptop: headless Chromium, a scratch API and vite there.
#   deploy/e2e-a1.sh [user@host]          (default ubuntu@79.72.56.0)
# One-time setup on the node (done 19 Nov 2026): Node 20 in /opt/node (nodejs.org's arm64 build, SHA-256 checked),
# `npx playwright install-deps chromium` (root) and `npx playwright install chromium` (as sky), the checkout /home/sky/e2e,
# and the scratch store /var/lib/sky-e2e: store/ links to the node's own store (index, products, caches) except findings.db (a
# read-only copy of the e2e one); product-base.sqlite (the e2e product database every run starts from); clips/.
# Local runs are allowed too (the user, 19 Nov 2026), with everything stopped after each: this script is for when the Mac stays idle.
# Each run: sync this checkout, start the API (8790, dev sign-in, no sweep or warm set) and vite (5190) there from a fresh product
# database, warm the e2e track's field and film, run the e2e, then stop both whatever happened. Exit code: the e2e's.
set -euo pipefail
HOST="${1:-ubuntu@79.72.56.0}"
KEY="${SKY_SSH_KEY:-$HOME/.ssh/oci_spherex_ed25519}"
SSH="ssh -i $KEY -o ConnectTimeout=20"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
rsync -az --delete -e "$SSH" --rsync-path="sudo -u sky rsync" \
  --exclude .git --exclude .venv --exclude .store --exclude store --exclude 'web/node_modules' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude 'web/test-results' --exclude 'web/playwright-report' --exclude '.claude' \
  --exclude clips --exclude 'product.sqlite*' ./ "$HOST:/home/sky/e2e/"
$SSH "$HOST" 'sudo -u sky bash -s' <<'REMOTE'
set -uo pipefail
export PATH=/opt/node/bin:$PATH
E=/var/lib/sky-e2e
cd /home/sky/e2e
lock=$(sha256sum web/package-lock.json | cut -c1-16)
if [ "$(cat web/node_modules/.lock-hash 2>/dev/null)" != "$lock" ]; then (cd web && npm ci --no-audit --no-fund -q && echo "$lock" > node_modules/.lock-hash); fi
~/.local/bin/uv sync --frozen --no-dev -q
cp "$E/product-base.sqlite" "$E/product.sqlite"; rm -f "$E/product.sqlite-wal" "$E/product.sqlite-shm"
stop() {  # by pattern: vite runs under npm and sh, so its own PID is not the server's
  kill "$API" "$VITE" 2>/dev/null; sleep 1
  pkill -u sky -f "uvicorn service.app:app --host 127.0.0.1 --port 8790" 2>/dev/null
  pkill -u sky -f "vite --port 5190" 2>/dev/null
  pkill -u sky -f "chrome-headless-shell" 2>/dev/null
  pkill -u sky -f "playwright" 2>/dev/null
  true
}
env -i HOME=/home/sky PATH="$PATH" SKY_FINDINGS_READONLY=1 SKY_AUTH_DEV=1 SKY_STORE=$E/store SKY_CLIPS=$E/clips \
  SKY_PRODUCT_DB=$E/product.sqlite SKY_WARM=0 SKY_SWEEP=0 SKY_WORKERS=2 \
  .venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8790 >$E/api.log 2>&1 &
API=$!
(cd web && env SKY_API=http://127.0.0.1:8790 SKY_TILES=https://www.spherex.space npx vite --port 5190 --strictPort >$E/vite.log 2>&1) &
VITE=$!
trap stop EXIT
for i in $(seq 1 120); do curl -sf -m 3 http://127.0.0.1:8790/api/v1/health >/dev/null && curl -sf -m 3 http://127.0.0.1:5190/ >/dev/null && break; sleep 2; done
# the e2e's track: its task's field and the film of its pass, built once (the node's products keep them for the next runs)
.venv/bin/python - <<'PY'
import json, time, urllib.request
B = "http://127.0.0.1:8790"
def get(u):
    with urllib.request.urlopen(B + u, timeout=120) as r:
        return json.load(r)
t = get("/api/tasks/next?kind=finding&finding_id=film:2e26f8ee6415")["task"]
mjd = (get("/api/v1/findings/film:2e26f8ee6415").get("data") or {}).get("film")
t0 = time.time()
for u in (f"/api/v1/fields?ra={t['ra']}&dec={t['dec']}&fov={t['fov']}", f"/api/v1/film?ra={t['ra']}&dec={t['dec']}&fov={t['fov']}&mjd={mjd}"):
    while time.time() - t0 < 1500:
        d = get(u)
        if d.get("state") in ("done", "failed", "error", "none"):
            break
        time.sleep(10)
    print("warm", u.split("?")[0], d.get("state"), round(time.time() - t0), "s", flush=True)
PY
cd web && SKY_FLOW_URL=http://127.0.0.1:5190 npx playwright test -c playwright.flow.config.ts 2>&1 | tail -25
exit "${PIPESTATUS[0]}"
REMOTE
