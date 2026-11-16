"""The background sweep of the sky grid, run as its own process (see service/sweep.py for what it does and the memory cap).

    # a local trial on a few squares of the Planet Nine band, in a private store beside the worktree:
    .venv/bin/python tools/sweep.py --store sweep-store --shared-store ../Planet-X-SPHEREx/store --squares 5 --cap-mb 6000

    # import the places already searched into the grid (the list of /api/v1/searched, a file or a URL):
    .venv/bin/python tools/sweep.py --import-searched data/searched_seed.json

The square states go to the product database (SKY_PRODUCT_DB, default <store>/product.sqlite). It stops after --squares squares or
--hours hours, and at once (exit code 3) when its processes go over --cap-mb of resident memory. It resumes where it stopped: a
searched square is never searched again (a failed one only with --retry-failed). NOT started on production in this round.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from service import grid, sweep
from service.grid import GridDB
from service.storage import product_db_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=Path("sweep-store"), help="the store the sweep's instance works in")
    ap.add_argument("--shared-store", type=Path, default=None, help="copy the exposure index from this store (local trial)")
    ap.add_argument("--db", type=Path, default=None, help="the product database (default SKY_PRODUCT_DB or <store>/product.sqlite)")
    ap.add_argument("--squares", type=int, default=5)
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--cap-mb", type=float, default=6000.0, help="hard cap on the resident memory of the sweep's processes")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--recycle-every", type=int, default=10, help="start the instance afresh after this many squares")
    ap.add_argument("--per-search-minutes", type=float, default=20.0)
    ap.add_argument("--tiers", default="0,1,2", help="0 Planet Nine band, 1 the rest of the ecliptic, 2 everything else")
    ap.add_argument("--cells", default="", help="search these square ids instead of the sweep order (comma separated)")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--standby", action="store_true", help="run the instance as a standby (production: the main instance's Findings)")
    ap.add_argument("--systemd-scope", action="store_true", help="Linux: also put the instance in a systemd scope with MemoryMax")
    ap.add_argument("--base", default=None, help="use a running service instead of starting one (no memory cap then)")
    ap.add_argument("--import-searched", default=None, help="a /api/v1/searched JSON file or URL to import, then exit")
    ap.add_argument("--out", type=Path, default=None, help="write the run's summary (rate, peak memory, per square) here")
    a = ap.parse_args()

    db = GridDB(a.db or product_db_path(a.store.resolve()))
    if a.import_searched:
        src = a.import_searched
        if src.startswith("http"):
            import httpx

            body = httpx.get(src, timeout=60).json()
        else:
            body = json.loads(Path(src).read_text())
        n = grid.import_searched(db, body.get("fields", []))
        print(f"imported {n} searched places into {db.path}; counts {db.counts()}")
        return 0

    sweep.prepare_store(a.store.resolve(), a.shared_store.resolve() if a.shared_store else None)
    if a.cells:
        cells: Iterable[grid.Cell] = [grid.by_id(c.strip()) for c in a.cells.split(",") if c.strip()]
    else:
        cells = sweep.pending(db, grid.sweep_order(tuple(int(t) for t in a.tiers.split(","))), a.retry_failed)
    r = sweep.run(
        db,
        a.store.resolve(),
        cells,
        a.squares,
        a.cap_mb,
        workers=a.workers,
        per_search_s=a.per_search_minutes * 60.0,
        hours=a.hours,
        standby=a.standby,
        systemd_scope=a.systemd_scope,
        base=a.base,
        recycle_every=a.recycle_every,
    )
    s = sweep.summary_json(r)
    print(s)
    if a.out:
        a.out.write_text(s)
    return sweep.STOP_MEMORY if r.stopped == "memory cap" else 0


if __name__ == "__main__":
    sys.exit(main())
