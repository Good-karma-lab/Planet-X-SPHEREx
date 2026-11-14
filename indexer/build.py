"""Build and keep the exposure index.

    uv run python -m indexer.build bootstrap [--index store/index/exposures.sqlite] [--workers 4]
    uv run python -m indexer.build reconcile           # counts per period: bucket listing vs index -> report
    uv run python -m indexer.build query RA DEC FOV_ARCMIN

`bootstrap` is resumable: days already fetched are skipped, except the last `--refresh-days` (new files arrive there).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import httpx

from indexer.irsa import fetch_day
from indexer.store import Index
from service.reader.s3 import BUCKET_URL, USER_AGENT

FIRST_DAY = 60789  # 2025-04-24, the first SPHEREx survey exposures
DEFAULT_INDEX = Path("store/index/exposures.sqlite")
RELEASES = ("dr1", "qr3")


def today_mjd() -> int:
    return int(time.time() / 86400.0 + 40587.0)


def bootstrap(index: Index, workers: int, first: int, last: int, refresh_days: int, log: bool = True) -> int:
    done = index.days_done()
    todo = [d for d in range(first, last + 1) if d not in done or d > last - refresh_days]
    total = 0
    t0 = time.time()
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client, ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(_fetch_with_retry, client, d): d for d in todo}
        for k, fut in enumerate(as_completed(futures), 1):
            d = futures[fut]
            rows = fut.result()
            n = index.add(rows)
            index.mark_day(d, len(rows), datetime.now(UTC).isoformat(timespec="seconds"))
            index.commit()
            total += n
            if log and (k % 10 == 0 or k == len(todo)):
                print(f"day {k}/{len(todo)} (MJD {d}): +{n} files, index {index.count():,}, {time.time() - t0:.0f} s", flush=True)
    index.set_meta("bootstrap_at", datetime.now(UTC).isoformat(timespec="seconds"))
    index.set_meta("source", "IRSA TAP spherex.artifact + spherex.plane")
    index.commit()
    return total


def _fetch_with_retry(client: httpx.Client, day: int, tries: int = 4) -> list:  # type: ignore[type-arg]
    for k in range(tries):
        try:
            return fetch_day(client, day)
        except (httpx.HTTPError, RuntimeError):
            if k == tries - 1:
                raise
            time.sleep(5 * (k + 1))
    return []


def list_counts(client: httpx.Client, release: str) -> dict[str, int]:
    """Number of .fits files per period of a release in the bucket (ListObjectsV2, paginated)."""
    s = client.get(BUCKET_URL, params={"list-type": "2", "delimiter": "/", "prefix": f"{release}/level2/"}).text
    periods = re.findall(r"<Prefix>[^<]*/level2/([^<]+)/</Prefix>", s)
    out = {}
    for p in periods:
        n, token = 0, None
        while True:
            params = {"list-type": "2", "prefix": f"{release}/level2/{p}/"}
            if token:
                params["continuation-token"] = token
            t = client.get(BUCKET_URL, params=params, timeout=60).text
            n += len(re.findall(r"<Key>[^<]*\.fits</Key>", t))
            m = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", t)
            if not m:
                break
            token = m.group(1)
        out[p] = n
    return out


def reconcile(index: Index) -> dict[str, object]:
    have = index.counts_by_period()
    report: dict[str, object] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "releases": {}}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for rel in RELEASES:
            bucket = list_counts(client, rel)
            diffs = {p: {"bucket": n, "index": have.get((rel, p), 0)} for p, n in bucket.items() if n != have.get((rel, p), 0)}
            report["releases"][rel] = {  # type: ignore[index]
                "periods": len(bucket),
                "files_bucket": sum(bucket.values()),
                "files_index": sum(v for (r, _), v in have.items() if r == rel),
                "periods_differing": diffs,
            }
    index.set_meta("reconcile", json.dumps(report))
    index.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="indexer.build")
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--first", type=int, default=FIRST_DAY)
    b.add_argument("--last", type=int, default=None)
    b.add_argument("--refresh-days", type=int, default=3)
    sub.add_parser("reconcile")
    q = sub.add_parser("query")
    q.add_argument("ra", type=float)
    q.add_argument("dec", type=float)
    q.add_argument("fov", type=float, help="field side in arcmin")
    a = ap.parse_args(argv)
    index = Index(a.index)
    try:
        if a.cmd == "bootstrap":
            n = bootstrap(index, a.workers, a.first, a.last or today_mjd(), a.refresh_days)
            print(f"added {n:,} files; index holds {index.count():,}")
        elif a.cmd == "reconcile":
            print(json.dumps(reconcile(index), indent=1)[:4000])
        else:
            t = time.time()
            rows = index.overlapping(a.ra, a.dec, a.fov / 60.0 / 2.0 * 2**0.5)
            print(f"{len(rows)} exposures in {1000 * (time.time() - t):.0f} ms")
            for e in rows[:10]:
                print(e)
    finally:
        index.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
