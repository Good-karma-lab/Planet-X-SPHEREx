#!/usr/bin/env bash
# Copy a consistent snapshot of the index and the pinned (warm-set) products to R2 (only new immutable files are uploaded).
set -euo pipefail
STORE="${SKY_STORE:-/var/lib/sky}"
cd /opt/sky
~/.local/bin/uv run --no-dev python -m service.snapshot list "$STORE" > /tmp/sky-snapshot-files.txt
sqlite3 "$STORE/index/exposures.sqlite" ".backup '$STORE/index/exposures.snapshot.sqlite'"
sqlite3 "$STORE/catalogue.sqlite" ".backup '$STORE/catalogue.snapshot.sqlite'"
echo "index/exposures.snapshot.sqlite" >> /tmp/sky-snapshot-files.txt
rclone copy --files-from /tmp/sky-snapshot-files.txt --transfers 8 "$STORE" r2:sky-warm/store
