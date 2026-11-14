#!/usr/bin/env bash
# Restore the index and the warm set from R2 onto a fresh VM (before the API serves, or while it runs).
set -euo pipefail
STORE="${SKY_STORE:-/var/lib/sky}"
mkdir -p "$STORE/index"
rclone copy --transfers 16 r2:sky-warm/store "$STORE"
if [ -f "$STORE/index/exposures.snapshot.sqlite" ] && [ ! -f "$STORE/index/exposures.sqlite" ]; then
  mv "$STORE/index/exposures.snapshot.sqlite" "$STORE/index/exposures.sqlite"
fi
if [ -f "$STORE/catalogue.snapshot.sqlite" ] && [ ! -f "$STORE/catalogue.sqlite" ]; then
  mv "$STORE/catalogue.snapshot.sqlite" "$STORE/catalogue.sqlite"
fi
echo "restored into $STORE"
