"""The exposure index: SQLite with an R*Tree over the footprints' bounding boxes, exact overlap tests in numpy.

One file per index snapshot (`store/index/exposures.sqlite`); WAL mode while building; copied as a whole for R2 snapshots.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from engine.spx.footprint import Footprint, contains, overlaps_circle, radec_to_xyz
from indexer.irsa import Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  release TEXT NOT NULL, period TEXT NOT NULL, pipeline TEXT NOT NULL,
  seq INTEGER NOT NULL, grp INTEGER NOT NULL, det INTEGER NOT NULL,
  mjd0 REAL NOT NULL, mjd1 REAL NOT NULL,
  size INTEGER NOT NULL, md5 TEXT NOT NULL,
  cra REAL NOT NULL, cdec REAL NOT NULL, radius REAL NOT NULL,
  poly BLOB NOT NULL,
  source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_mjd ON files(mjd0);
CREATE INDEX IF NOT EXISTS files_period ON files(release, period);
CREATE VIRTUAL TABLE IF NOT EXISTS boxes USING rtree(id, ra0, ra1, dec0, dec1, +fid INTEGER);
CREATE TABLE IF NOT EXISTS days (day INTEGER PRIMARY KEY, n INTEGER NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

# The IRSA footprints are 4-corner polygons: the detector edge between corners bows outward by the SIP distortion. Overlap tests add
# this margin (measured in the M2 validation: see docs/validation/).
EDGE_MARGIN_DEG = 60.0 / 3600.0


@dataclass(frozen=True)
class Exposure:
    id: int
    key: str
    release: str
    period: str
    seq: int
    group: int
    det: int
    mjd0: float
    mjd1: float
    size: int
    md5: str


def footprint_of(ra: Sequence[float], dec: Sequence[float]) -> Footprint:
    v = radec_to_xyz(np.asarray(ra, float), np.asarray(dec, float))
    c = v.mean(axis=0)
    c /= np.linalg.norm(c)
    cra = math.degrees(math.atan2(c[1], c[0])) % 360.0
    cdec = math.degrees(math.asin(max(-1.0, min(1.0, c[2]))))
    radius = math.degrees(math.acos(max(-1.0, min(1.0, float((v @ c).min())))))
    return Footprint(np.asarray(ra, float), np.asarray(dec, float), (cra, cdec), radius)


def boxes_for(cra: float, cdec: float, radius: float) -> list[tuple[float, float, float, float]]:
    """RA/Dec boxes (RA split at 0/360) that hold a circle on the sky."""
    d0, d1 = cdec - radius, cdec + radius
    if d1 >= 90.0 or d0 <= -90.0:
        return [(0.0, 360.0, max(d0, -90.0), min(d1, 90.0))]
    half = math.degrees(math.asin(min(1.0, math.sin(math.radians(radius)) / math.cos(math.radians(max(abs(d0), abs(d1)))))))
    a0, a1 = cra - half, cra + half
    if half >= 180.0:
        return [(0.0, 360.0, d0, d1)]
    if a0 < 0:
        return [(0.0, a1, d0, d1), (a0 + 360.0, 360.0, d0, d1)]
    if a1 > 360.0:
        return [(a0, 360.0, d0, d1), (0.0, a1 - 360.0, d0, d1)]
    return [(a0, a1, d0, d1)]


class Index:
    def __init__(self, path: Path, readonly: bool = False) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        uri = f"file:{path}?mode=ro" if readonly else f"file:{path}"
        self.db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---- building ---------------------------------------------------------------------------------------------------------------
    def add(self, rows: Iterable[Row], source: str = "irsa") -> int:
        n = 0
        cur = self.db.cursor()
        for r in rows:
            fp = footprint_of(r.ra, r.dec)
            poly = np.asarray([r.ra, r.dec], np.float64).tobytes()
            cur.execute(
                "INSERT INTO files(key, release, period, pipeline, seq, grp, det, mjd0, mjd1, size, md5, cra, cdec, radius, poly, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO NOTHING",
                (
                    r.key,
                    r.release,
                    r.period,
                    r.pipeline,
                    r.seq,
                    r.group,
                    r.det,
                    r.mjd0,
                    r.mjd1,
                    r.size,
                    r.md5,
                    fp.centre[0],
                    fp.centre[1],
                    fp.radius_deg,
                    poly,
                    source,
                ),
            )
            if cur.rowcount == 1:
                fid = cur.lastrowid
                for box in boxes_for(fp.centre[0], fp.centre[1], fp.radius_deg + EDGE_MARGIN_DEG):
                    cur.execute("INSERT INTO boxes(ra0, ra1, dec0, dec1, fid) VALUES (?,?,?,?,?)", (*box, fid))
                n += 1
        return n

    def mark_day(self, day: int, n: int, when: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?)", (day, n, when))

    def days_done(self) -> set[int]:
        return {int(d) for (d,) in self.db.execute("SELECT day FROM days")}

    def commit(self) -> None:
        self.db.commit()

    def set_meta(self, k: str, v: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))

    def meta(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT k, v FROM meta").fetchall())

    # ---- queries ----------------------------------------------------------------------------------------------------------------
    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0])

    def counts_by_period(self) -> dict[tuple[str, str], int]:
        return {(r, p): n for r, p, n in self.db.execute("SELECT release, period, COUNT(*) FROM files GROUP BY release, period")}

    def overlapping(self, ra: float, dec: float, radius_deg: float) -> list[Exposure]:
        """Exposures whose footprint overlaps a circle on the sky (the field's circumscribed circle), in time order."""
        ids: set[int] = set()
        for a0, a1, d0, d1 in boxes_for(ra, dec, radius_deg):
            ids.update(
                i
                for (i,) in self.db.execute(
                    "SELECT fid FROM boxes WHERE ra1 >= ? AND ra0 <= ? AND dec1 >= ? AND dec0 <= ?", (a0, a1, d0, d1)
                )
            )
        if not ids:
            return []
        out = []
        q = ",".join("?" * len(ids))
        sql = f"SELECT id, key, release, period, seq, grp, det, mjd0, mjd1, size, md5, cra, cdec, radius, poly FROM files WHERE id IN ({q})"
        for fid, key, rel, per, seq, grp, det, m0, m1, size, md5, cra, cdec, rad, poly in self.db.execute(sql, tuple(ids)):
            pv = np.frombuffer(poly, np.float64).reshape(2, -1)
            fp = Footprint(pv[0], pv[1], (cra, cdec), rad)
            if overlaps_circle(fp, ra, dec, radius_deg + EDGE_MARGIN_DEG):
                out.append(Exposure(fid, key, rel, per, seq, grp, det, m0, m1, size, md5))
        out.sort(key=lambda e: (e.mjd0, e.det))
        return out

    def containing(self, ra: float, dec: float, mjd0: float, mjd1: float) -> list[Exposure]:
        """Exposures whose footprint holds a point (no margin) and whose time overlaps [mjd0, mjd1]."""
        ids: set[int] = set()
        for a0, a1, d0, d1 in boxes_for(ra, dec, 1.0 / 3600.0):
            ids.update(
                i
                for (i,) in self.db.execute(
                    "SELECT fid FROM boxes WHERE ra1 >= ? AND ra0 <= ? AND dec1 >= ? AND dec0 <= ?", (a0, a1, d0, d1)
                )
            )
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        sql = (
            "SELECT id, key, release, period, seq, grp, det, mjd0, mjd1, size, md5, cra, cdec, radius, poly FROM files "
            f"WHERE id IN ({q}) AND mjd1 >= ? AND mjd0 <= ?"
        )
        out = []
        for fid, key, rel, per, seq, grp, det, m0, m1, size, md5, cra, cdec, rad, poly in self.db.execute(sql, (*ids, mjd0, mjd1)):
            pv = np.frombuffer(poly, np.float64).reshape(2, -1)
            if contains(Footprint(pv[0], pv[1], (cra, cdec), rad), ra, dec):
                out.append(Exposure(fid, key, rel, per, seq, grp, det, m0, m1, size, md5))
        return out
