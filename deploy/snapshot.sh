#!/usr/bin/env bash
# Copy a consistent snapshot off the machine: the people's data (accounts, marks, tasks, findings and what catalogues said: product.sqlite,
# findings.db), the product catalogues, the exposure index and the pinned (warm-set) products. Only new immutable product files are
# uploaded; the databases go up as dated copies (the last SKY_SNAPSHOT_KEEP kept) and as "latest". The target is an rclone remote:
# SKY_SNAPSHOT_REMOTE (default r2:sky-warm), set up once with `rclone config` (Cloudflare R2, or OCI Object Storage's S3 endpoint).
set -euo pipefail
STORE="${SKY_STORE:-/var/lib/sky}"
REMOTE="${SKY_SNAPSHOT_REMOTE:-r2:sky-warm}"
KEEP="${SKY_SNAPSHOT_KEEP:-14}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SNAP="$STORE/snapshot-db"
mkdir -p "$SNAP"
cd /opt/sky
# the databases, each by its own path (two files are called catalogue.sqlite), consistent while the service runs
sqlite3 "$STORE/product.sqlite" ".backup '$SNAP/product.sqlite'"
sqlite3 "$STORE/findings.db" ".backup '$SNAP/findings.db'"
sqlite3 "$STORE/catalogue.sqlite" ".backup '$SNAP/catalogue.sqlite'"
[ -f "$STORE/clips/catalogue.sqlite" ] && sqlite3 "$STORE/clips/catalogue.sqlite" ".backup '$SNAP/clips-catalogue.sqlite'"
[ -f "$STORE/shadow.db" ] && sqlite3 "$STORE/shadow.db" ".backup '$SNAP/shadow.db'"
sqlite3 "$STORE/index/exposures.sqlite" ".backup '$STORE/index/exposures.snapshot.sqlite'"
rclone copy --transfers 4 "$SNAP" "$REMOTE/db/$TS"
rclone sync --transfers 4 "$SNAP" "$REMOTE/db/latest"
# the dated copies beyond the last KEEP go
rclone lsf --dirs-only "$REMOTE/db" | grep -E '^[0-9]{8}T' | sort | head -n "-$KEEP" | while read -r d; do rclone purge "$REMOTE/db/$d"; done
# the index and the warm set (immutable files: only the new ones go up)
~/.local/bin/uv run --no-dev python -m service.snapshot list "$STORE" > /tmp/sky-snapshot-files.txt
echo "index/exposures.snapshot.sqlite" >> /tmp/sky-snapshot-files.txt
rclone copy --files-from /tmp/sky-snapshot-files.txt --transfers 8 "$STORE" "$REMOTE/store"
echo "snapshot $TS -> $REMOTE"
