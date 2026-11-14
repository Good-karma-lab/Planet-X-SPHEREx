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

from indexer.irsa import NAME, Row, fetch_day
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
    failed: list[int] = []
    t0 = time.time()
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client, ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(_fetch_with_retry, client, d): d for d in todo}
        for k, fut in enumerate(as_completed(futures), 1):
            d = futures[fut]
            try:
                rows = fut.result()
            except (httpx.HTTPError, RuntimeError) as err:  # IRSA did not answer: the day stays to do; reconcile crawls the bucket
                failed.append(d)
                if log:
                    print(f"MJD {d}: IRSA did not answer ({type(err).__name__}); left for the next run", flush=True)
                continue
            n = index.add(rows)
            index.mark_day(d, len(rows), datetime.now(UTC).isoformat(timespec="seconds"))
            index.commit()
            total += n
            if log and (k % 10 == 0 or k == len(todo)):
                print(f"day {k}/{len(todo)} (MJD {d}): +{n} files, index {index.count():,}, {time.time() - t0:.0f} s", flush=True)
    index.set_meta("bootstrap_at", datetime.now(UTC).isoformat(timespec="seconds"))
    index.set_meta("bootstrap_days_failed", json.dumps(failed))
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
            time.sleep((10, 30, 60)[min(k, 2)])
    return []


def list_keys(client: httpx.Client, release: str) -> dict[str, list[str]]:
    """The .fits keys of every period of a release in the bucket (ListObjectsV2, paginated)."""
    s = client.get(BUCKET_URL, params={"list-type": "2", "delimiter": "/", "prefix": f"{release}/level2/"}).text
    periods = re.findall(r"<Prefix>[^<]*/level2/([^<]+)/</Prefix>", s)
    out: dict[str, list[str]] = {}
    for p in periods:
        keys: list[str] = []
        token = None
        while True:
            params = {"list-type": "2", "prefix": f"{release}/level2/{p}/"}
            if token:
                params["continuation-token"] = token
            t = client.get(BUCKET_URL, params=params, timeout=60).text
            keys += re.findall(r"<Key>([^<]*\.fits)</Key>", t)
            m = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", t)
            if not m:
                break
            token = m.group(1)
        out[p] = keys
    return out


def crawl_rows(client: httpx.Client, keys: list[str], workers: int = 16) -> list[Row]:
    """Index rows read from the files themselves (the first 32 kB: primary and IMAGE headers) for files IRSA did not list."""
    from engine.spx.fitsio import hdu_at, header_length
    from engine.spx.footprint import footprint

    def one(key: str) -> Row | None:
        m = NAME.match(key)
        if not m:
            return None
        buf = client.get(BUCKET_URL + key, headers={"Range": "bytes=0-65535"}, timeout=60).content
        n0 = header_length(buf)
        if n0 is None:
            return None
        h = hdu_at(1, n0, buf[n0:]).header
        fp = footprint(h)
        mjd = float(h["MJD-AVG"])
        m0, m1 = float(h.get("MJD-BEG", mjd)), float(h.get("MJD-END", mjd))
        return Row(
            key,
            m["release"],
            m["period"],
            m["pipeline"],
            int(m["seq"]),
            int(m["group"]),
            int(m["det"]),
            m0,
            m1,
            0,
            "",
            tuple(float(x) for x in fp.ra),
            tuple(float(x) for x in fp.dec),
        )

    with ThreadPoolExecutor(workers) as pool:
        return [r for r in pool.map(one, keys) if r is not None]


def reconcile(index: Index, crawl: bool = True) -> dict[str, object]:
    """Counts per period, bucket against index (and the source of the index rows); with `crawl`, the files of the bucket that the
    index lacks are read from their headers and added (source "header"), so the index never depends on IRSA being complete or up."""
    report: dict[str, object] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "releases": {}}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for rel in RELEASES:
            bucket = list_keys(client, rel)
            crawled = 0
            if crawl:
                have = {k for (k,) in index.db.execute("SELECT key FROM files WHERE release = ?", (rel,))}
                missing = [k for keys in bucket.values() for k in keys if k not in have]
                if missing:
                    crawled = index.add(crawl_rows(client, missing), source="header")
                    index.commit()
            by = {
                (p, src): n
                for p, src, n in index.db.execute(
                    "SELECT period, source, COUNT(*) FROM files WHERE release = ? GROUP BY period, source", (rel,)
                )
            }
            periods = {}
            for p, keys in sorted(bucket.items()):
                irsa, header = by.get((p, "irsa"), 0), by.get((p, "header"), 0)
                periods[p] = {"bucket": len(keys), "index": irsa + header, "from_irsa": irsa, "from_headers": header}
            report["releases"][rel] = {  # type: ignore[index]
                "periods": len(bucket),
                "files_bucket": sum(len(v) for v in bucket.values()),
                "files_index": sum(v["index"] for v in periods.values()),
                "added_from_headers_now": crawled,
                "periods_differing": {p: v for p, v in periods.items() if v["bucket"] != v["index"]},
                "per_period": periods,
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
    rc = sub.add_parser("reconcile")
    rc.add_argument("--no-crawl", action="store_true", help="only count, do not read the headers of files IRSA did not list")
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
            rep = reconcile(index, crawl=not a.no_crawl)
            for rel, v in rep["releases"].items():  # type: ignore[attr-defined]
                print(
                    rel,
                    {k: v[k] for k in ("periods", "files_bucket", "files_index", "added_from_headers_now")},
                    "differing:",
                    list(v["periods_differing"].items())[:5],
                )
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
