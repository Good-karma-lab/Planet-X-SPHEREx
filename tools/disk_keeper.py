"""Keeps the production disk from filling: run every hour by a systemd timer (deploy/systemd/sky-disk.timer).

    uv run python tools/disk_keeper.py --store /var/lib/sky [--dry-run] [--high 75] [--low 60]

Every run, whatever the fill:
  tmp/                     files older than one day are deleted;
  clips/                   clips of finds that no longer exist in findings.db, and clip versions that are not current;
  /var/lib/sky-backup      only the newest 7 backups are kept.
When the disk (the file system of the store) is fuller than --high per cent, until it is below --low:
  1. raw/bands/            the cached rows of SPHEREx files, least recently used first (they are read again from NASA's bucket);
  2. products/field/       only when raw/bands is already small (under --raw-floor GB): finished fields, least recently used
                           first, never a pinned one (the warm set), never one at the five test targets, never one that holds a
                           finding or a person's mark or investigation, never one used in the last --busy-hours hours.
Never touched: findings.db, product.sqlite, catalogue.sqlite (except the rows of the fields it deletes), index/, fpx_known/,
raw/calib, raw/psf, raw/headers, /etc/sky.

It prints one JSON line per run (to the journal): the fill before and after, and the bytes freed by folder. With --alert-pct (85) it
sends one e-mail a day at most through Resend (SKY_RESEND_API_KEY, SKY_MAIL_FROM) to --mail-to when the disk is fuller than that
or the run failed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

DAY = 86400.0
KEEP_BACKUPS = 7
TARGET_RADIUS_DEG = 0.6  # a field whose centre is this close to a test target is never deleted
REPO = Path(__file__).resolve().parent.parent


@dataclass
class Report:
    dry_run: bool
    store: str
    used_pct_before: float = 0.0
    used_pct_after: float = 0.0
    free_gb_after: float = 0.0
    freed: dict[str, int] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def add(self, folder: str, nbytes: int, n: int = 1) -> None:
        self.freed[folder] = self.freed.get(folder, 0) + nbytes
        self.files[folder] = self.files.get(folder, 0) + n

    def line(self) -> str:
        d = self.__dict__ | {"freed_gb": {k: round(v / 1e9, 3) for k, v in self.freed.items()}}
        d.pop("freed")
        return json.dumps(d, separators=(",", ":"))


def usage(path: Path) -> tuple[float, float, int]:
    """(used per cent, free GB, total bytes) of the file system that holds `path`."""
    u = shutil.disk_usage(path)
    return 100.0 * (u.total - u.free) / u.total, u.free / 1e9, u.total


def walk_files(root: Path) -> Iterator[os.DirEntry[str]]:
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        yield e
        except OSError:
            continue


def remove(path: Path, rep: Report, folder: str, nbytes: int) -> None:
    if not rep.dry_run:
        try:
            path.unlink()
        except OSError:
            return
    rep.add(folder, nbytes)


def clean_tmp(store: Path, rep: Report, now: float) -> None:
    for e in walk_files(store / "tmp"):
        st = e.stat(follow_symlinks=False)
        if now - st.st_mtime > DAY:
            remove(Path(e.path), rep, "tmp", st.st_size)


def clean_backups(backups: Path, rep: Report) -> None:
    if not backups.is_dir():
        return
    dirs = sorted((d for d in backups.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    for d in dirs[KEEP_BACKUPS:]:
        size = sum(e.stat(follow_symlinks=False).st_size for e in walk_files(d))
        if not rep.dry_run:
            shutil.rmtree(d, ignore_errors=True)
        rep.add("backups", size)
    rep.kept["backups"] = min(len(dirs), KEEP_BACKUPS)


def finding_ids(store: Path) -> set[str] | None:
    """The ids of every find in findings.db (read only), or None when it cannot be read (then no clip is deleted)."""
    db = store / "findings.db"
    if not db.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as c:
            return {r[0] for r in c.execute("SELECT id FROM finds")}
    except sqlite3.Error:
        return None


def clean_clips(clips: Path, store: Path, current_version: str | None, rep: Report) -> None:
    """Clip files of finds that no longer exist, and whole clip-version folders that are not current."""
    if not clips.is_dir():
        return
    ids = finding_ids(store)
    for vdir in clips.iterdir():
        if not vdir.is_dir() or not (len(vdir.name) <= 4 and vdir.name.startswith("c") and vdir.name[1:].isdigit()):
            continue  # the clips' own field store (products/, catalogue.sqlite) is not a version folder
        if current_version and vdir.name != current_version:
            for e in walk_files(vdir):
                remove(Path(e.path), rep, "clips", e.stat(follow_symlinks=False).st_size)
            if not rep.dry_run:
                shutil.rmtree(vdir, ignore_errors=True)
            continue
        if ids is None:
            continue
        safe = {i.replace(":", "_").replace("/", "_") for i in ids}  # the file name of a find's clip (service.clips.Clips.path)
        for e in walk_files(vdir):
            stem = e.name.rsplit(".", 1)[0]
            if stem.startswith("place_"):
                continue  # a clip of a place, not of a find
            stem = stem.removesuffix("_still")
            if stem not in safe:
                remove(Path(e.path), rep, "clips", e.stat(follow_symlinks=False).st_size)


def dir_size(root: Path) -> int:
    return sum(e.stat(follow_symlinks=False).st_size for e in walk_files(root))


def clean_raw(store: Path, rep: Report, low_pct: float, now: float, busy_s: float) -> None:
    """raw/bands, least recently used (atime, else mtime) first, until the disk is below `low_pct`."""
    root = store / "raw" / "bands"
    if not root.is_dir():
        return
    used, _, total = usage(store)
    need = (used - low_pct) / 100.0 * total
    if need <= 0:
        return
    files = []
    for e in walk_files(root):
        st = e.stat(follow_symlinks=False)
        last = max(st.st_atime, st.st_mtime)
        if now - last < busy_s:
            continue  # read or written moments ago: a job may be using it
        files.append((last, st.st_size, e.path))
    files.sort()
    freed = 0
    for _, size, path in files:
        if freed >= need:
            break
        remove(Path(path), rep, "raw/bands", size)
        freed += size


def protected_places(store: Path) -> list[tuple[float, float]]:
    """Places whose fields are never deleted: the test targets, every find, every person's mark and investigation object."""
    places: list[tuple[float, float]] = []
    try:
        t = json.loads((REPO / "data" / "targets.json").read_text())
        for x in t["targets"] if isinstance(t, dict) else t:
            if x.get("ra_deg") is not None and x.get("dec_deg") is not None:
                places.append((float(x["ra_deg"]), float(x["dec_deg"])))
    except (OSError, ValueError, KeyError):
        pass
    for db, q in (
        (store / "findings.db", "SELECT ra, dec FROM finds"),
        (store / "product.sqlite", "SELECT ra, dec FROM acc_marks"),
    ):
        if not db.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as c:
                places += [(float(r[0]), float(r[1])) for r in c.execute(q)]
        except sqlite3.Error:
            return []  # cannot tell what is in use: protect everything (no field is deleted)
    try:
        with sqlite3.connect(f"file:{store / 'product.sqlite'}?mode=ro", uri=True, timeout=10) as c:
            for (items,) in c.execute("SELECT extra FROM acc_investigations"):
                for it in (json.loads(items or "{}") or {}).get("items", []) if items else []:
                    if it.get("ra") is not None and it.get("dec") is not None:
                        places.append((float(it["ra"]), float(it["dec"])))
    except (sqlite3.Error, ValueError, AttributeError):
        pass
    return places


def _sep_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    d_ra = (a[0] - b[0] + 180.0) % 360.0 - 180.0
    return math.hypot(d_ra * math.cos(math.radians(b[1])), a[1] - b[1])


def clean_fields(store: Path, rep: Report, low_pct: float, now: float, busy_s: float) -> None:
    """Finished, unpinned 'field' products, least recently used first, until the disk is below `low_pct`."""
    cat = store / "catalogue.sqlite"
    if not cat.is_file():
        return
    places = protected_places(store)
    if not places:
        rep.kept["fields_note"] = 1  # nothing could be read to protect: no field is deleted
        return
    used, _, total = usage(store)
    need = (used - low_pct) / 100.0 * total
    if need <= 0:
        return
    sys.path.insert(0, str(REPO))
    from service.products.versions import VERSIONS

    c = sqlite3.connect(cat, timeout=30, isolation_level=None)
    try:
        rows = c.execute(
            "SELECT key, version, params, accessed FROM products WHERE kind='field' AND complete=1 AND pinned=0 AND version=?"
            " ORDER BY accessed",
            (VERSIONS["field"],),
        ).fetchall()
        freed = 0
        kept = 0
        for key, version, params, accessed in rows:
            if freed >= need:
                break
            if now - float(accessed or 0) < busy_s:
                continue
            p = json.loads(params or "{}")
            centre = (float(p.get("ra", 0.0)), float(p.get("dec", 0.0)))
            reach = float(p.get("fov", 5.0)) / 60.0 * 0.75 + 1e-6
            if any(_sep_deg(q, centre) <= max(reach, TARGET_RADIUS_DEG if i < 5 else reach) for i, q in enumerate(places)):
                kept += 1
                continue
            d = store / "products" / "field" / version / key[:2] / key
            size = dir_size(d) if d.is_dir() else 0
            if not rep.dry_run:
                c.execute("DELETE FROM products WHERE key=?", (key,))
                shutil.rmtree(d, ignore_errors=True)
            rep.add("products/field", size)
            freed += size
        rep.kept["fields_protected"] = kept
    finally:
        c.close()


def alert(to: str, subject: str, text: str, stamp: Path, dry_run: bool) -> str:
    """One e-mail a day at most, through Resend; returns what happened (never the key)."""
    if dry_run:
        return "dry run: no mail"
    if stamp.is_file() and time.time() - stamp.stat().st_mtime < DAY:
        return "already sent today"
    key = os.environ.get("SKY_RESEND_API_KEY")
    if not key:
        return "no mail sender"
    import httpx

    sender = os.environ.get("SKY_MAIL_FROM") or "SPHEREx Space <noreply@mail.spherex.space>"
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            json={"from": sender, "to": [to], "subject": subject, "text": text},
            headers={"Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return f"mail failed: {type(e).__name__}"
    if r.status_code >= 300:
        return f"mail failed: {r.status_code}"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return "sent"


def run(a: argparse.Namespace) -> Report:
    store = Path(a.store).resolve()
    rep = Report(dry_run=a.dry_run, store=str(store))
    now = time.time()
    rep.used_pct_before = round(usage(store)[0], 1)
    clean_tmp(store, rep, now)
    clean_backups(Path(a.backups), rep)
    clean_clips(Path(a.clips) if a.clips else store / "clips", store, a.clip_version, rep)
    if usage(store)[0] > a.high or a.force:
        clean_raw(store, rep, a.low, now, a.busy_hours * 3600.0)
        raw_gb = dir_size(store / "raw" / "bands") / 1e9 if (store / "raw" / "bands").is_dir() else 0.0
        if a.dry_run:  # what the files counted for deletion would leave
            raw_gb -= rep.freed.get("raw/bands", 0) / 1e9
        if raw_gb < a.raw_floor and usage(store)[0] - (sum(rep.freed.values()) / usage(store)[2] * 100 if a.dry_run else 0) > a.low:
            clean_fields(store, rep, a.low, now, a.busy_hours * 3600.0)
    used, free_gb, total = usage(store)
    if a.dry_run:  # what the run would leave
        used -= sum(rep.freed.values()) / total * 100.0
        free_gb += sum(rep.freed.values()) / 1e9
    rep.used_pct_after, rep.free_gb_after = round(used, 1), round(free_gb, 1)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=os.environ.get("SKY_STORE", "/var/lib/sky"))
    ap.add_argument("--clips", default=os.environ.get("SKY_CLIPS"))
    ap.add_argument("--clip-version", default=None, help="the current clip version (default: the service's own)")
    ap.add_argument("--backups", default="/var/lib/sky-backup")
    ap.add_argument("--high", type=float, default=75.0, help="start deleting caches above this fill (per cent)")
    ap.add_argument("--low", type=float, default=60.0, help="stop when the fill is below this (per cent)")
    ap.add_argument("--raw-floor", type=float, default=5.0, help="fields are deleted only when raw/bands is smaller than this (GB)")
    ap.add_argument("--busy-hours", type=float, default=1.0, help="never delete what was used this recently")
    ap.add_argument("--alert-pct", type=float, default=85.0)
    ap.add_argument("--mail-to", default="good.karma.lab.ltd@gmail.com")
    ap.add_argument("--dry-run", action="store_true", help="count what would be deleted, delete nothing, send no mail")
    ap.add_argument("--force", action="store_true", help="clean the caches whatever the fill (tests)")
    a = ap.parse_args()
    if a.clip_version is None:
        sys.path.insert(0, str(REPO))
        try:
            from service.clips import VERSION as CLIP_VERSION

            a.clip_version = CLIP_VERSION
        except Exception:
            a.clip_version = None
    stamp = Path(a.store) / "disk_keeper" / "last_alert"
    try:
        rep = run(a)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}), flush=True)
        print(alert(a.mail_to, "SPHEREx Space: the disk keeper failed", f"The disk keeper run failed: {e}", stamp, a.dry_run), flush=True)
        return 1
    print(rep.line(), flush=True)
    if rep.used_pct_after > a.alert_pct:
        msg = f"The production disk is {rep.used_pct_after}% full ({rep.free_gb_after} GB free) after the keeper's run."
        print(json.dumps({"alert": alert(a.mail_to, "SPHEREx Space: the disk is nearly full", msg, stamp, a.dry_run)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
