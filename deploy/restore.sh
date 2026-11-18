#!/usr/bin/env bash
# Restore the people's data, the catalogues, the index and the warm set from the snapshot remote onto a fresh VM (before the API serves).
# SKY_SNAPSHOT_REMOTE as in snapshot.sh; SKY_SNAPSHOT_FROM picks a dated copy of the databases (default latest).
set -euo pipefail
STORE="${SKY_STORE:-/var/lib/sky}"
REMOTE="${SKY_SNAPSHOT_REMOTE:-r2:sky-warm}"
FROM="${SKY_SNAPSHOT_FROM:-latest}"
mkdir -p "$STORE/index" "$STORE/clips"
rclone copy --transfers 16 "$REMOTE/store" "$STORE"
if [ -f "$STORE/index/exposures.snapshot.sqlite" ] && [ ! -f "$STORE/index/exposures.sqlite" ]; then
  mv "$STORE/index/exposures.snapshot.sqlite" "$STORE/index/exposures.sqlite"
fi
TMP="$(mktemp -d)"
rclone copy "$REMOTE/db/$FROM" "$TMP"
for f in product.sqlite findings.db catalogue.sqlite; do
  [ -f "$TMP/$f" ] && [ ! -f "$STORE/$f" ] && mv "$TMP/$f" "$STORE/$f"
done
[ -f "$TMP/clips-catalogue.sqlite" ] && [ ! -f "$STORE/clips/catalogue.sqlite" ] && mv "$TMP/clips-catalogue.sqlite" "$STORE/clips/catalogue.sqlite"
rm -rf "$TMP"
echo "restored $FROM into $STORE (files already there were kept)"
