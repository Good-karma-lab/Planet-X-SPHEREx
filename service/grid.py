"""The fixed sky grid of squares: one square is one 20′ search field (the field the movers search, the brown-dwarf search and the
Time-lapse search take; tools/sky_scan.py and the background sweep search a square by asking for the 20′ field at its centre).

Scheme `dec20`: declination bands 20′ high from the south pole to the north pole (540 bands); band b is split into
n(b) = max(1, round(360 cos(dec_mid) / (1/3))) equal steps of right ascension, starting at RA 0. Every square is about 20′ × 20′ on
the sky (equal area within a few per cent, no square is thinner than 20′ in RA except the single cap squares), and the whole sky is
about 371 000 squares. A square's id is `"{band:03d}-{col:04d}"`; the scheme never changes, so ids are stable. The API gives the four
corners of each square, so a client never needs to know the scheme.

The square states and counters live in the product database (`SKY_PRODUCT_DB`, see service/storage.py): `new` (no row) → `queued`
→ `searching` → `searched` | `failed`, with the number of finds, the bar the search used, and the viewers counter (people who looked).
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEME = "dec20"
CELL_DEG = 1.0 / 3.0  # 20′: the search field
N_BANDS = 540  # 180° / 20′
FIELD_FOV_ARCMIN = 20.0
STATES = ("new", "queued", "searching", "searched", "failed")

# Order of the background sweep. The Planet Nine band: Brown & Batygin (2021, AJ 162, 219) put the most likely sky position of
# Planet Nine in a band along its predicted orbit plane, densest between RA ~1h and ~5h near the ecliptic (Cetus, Taurus, Eridanus);
# we take RA 15°–105°, ecliptic latitude −25°…+15°, searched from its centre (RA 50°, Dec +8°) outwards. Then the rest of the
# ecliptic (|β| ≤ 10°) by distance from that centre along the ecliptic, then everything else from the ecliptic outwards.
P9_RA = (15.0, 105.0)
P9_BETA = (-25.0, 15.0)
P9_CENTRE = (50.0, 8.0)
ECLIPTIC_BAND_DEG = 10.0
OBLIQUITY = math.radians(23.4392911)


def _n_cols(band: int) -> int:
    dec_mid = -90.0 + (band + 0.5) * CELL_DEG
    return max(1, round(360.0 * math.cos(math.radians(dec_mid)) / CELL_DEG))


_COLS = [_n_cols(b) for b in range(N_BANDS)]
TOTAL = sum(_COLS)


def cell_id(band: int, col: int) -> str:
    return f"{band:03d}-{col:04d}"


def parse_id(cid: str) -> tuple[int, int]:
    try:
        b, c = cid.split("-")
        band, col = int(b), int(c)
    except ValueError as e:
        raise ValueError(f"not a square id: {cid!r}") from e
    if not (0 <= band < N_BANDS and 0 <= col < _COLS[band]):
        raise ValueError(f"not a square id: {cid!r}")
    return band, col


@dataclass(frozen=True)
class Cell:
    id: str
    band: int
    col: int
    ra0: float
    ra1: float
    dec0: float
    dec1: float

    @property
    def ra(self) -> float:
        return (0.5 * (self.ra0 + self.ra1)) % 360.0

    @property
    def dec(self) -> float:
        return 0.5 * (self.dec0 + self.dec1)

    def corners(self) -> list[list[float]]:
        """Counter-clockwise on the sky seen from inside: SW, SE, NE, NW (RA increases to the east)."""
        return [
            [_r(self.ra0), round(self.dec0, 6)],
            [_r(self.ra1), round(self.dec0, 6)],
            [_r(self.ra1), round(self.dec1, 6)],
            [_r(self.ra0), round(self.dec1, 6)],
        ]


def _r(x: float) -> float:
    return round(x % 360.0, 6)


def cell(band: int, col: int) -> Cell:
    n = _COLS[band]
    w = 360.0 / n
    return Cell(cell_id(band, col), band, col, col * w, (col + 1) * w, -90.0 + band * CELL_DEG, -90.0 + (band + 1) * CELL_DEG)


def by_id(cid: str) -> Cell:
    return cell(*parse_id(cid))


def cell_at(ra: float, dec: float) -> Cell:
    band = min(N_BANDS - 1, max(0, math.floor((dec + 90.0) / CELL_DEG)))
    n = _COLS[band]
    col = math.floor((ra % 360.0) / (360.0 / n)) % n
    return cell(band, col)


def cells_near(ra: float, dec: float, radius_deg: float, limit: int = 5000) -> list[Cell]:
    """The squares whose centre lies within `radius_deg` of the place (and the square at the place itself), nearest first."""
    out: list[tuple[float, Cell]] = []
    b0 = max(0, math.floor((dec - radius_deg + 90.0) / CELL_DEG))
    b1 = min(N_BANDS - 1, math.floor((dec + radius_deg + 90.0) / CELL_DEG))
    for b in range(b0, b1 + 1):
        n = _COLS[b]
        w = 360.0 / n
        dec_mid = -90.0 + (b + 0.5) * CELL_DEG
        cosd = max(math.cos(math.radians(max(abs(dec_mid), 0.0))), 1e-6)
        span = radius_deg / cosd + w
        if span >= 180.0 or n <= 3:
            cols: Iterator[int] = iter(range(n))
        else:
            c0 = math.floor((ra - span) / w)
            c1 = math.floor((ra + span) / w)
            cols = iter(sorted({c % n for c in range(c0, c1 + 1)}))
        for c in cols:
            ce = cell(b, c)
            d = sep_deg(ra, dec, ce.ra, ce.dec)
            if d <= radius_deg:
                out.append((d, ce))
    here = cell_at(ra, dec)
    if all(ce.id != here.id for _, ce in out):
        out.append((0.0, here))
    out.sort(key=lambda t: t[0])
    return [ce for _, ce in out[:limit]]


def sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s))))


def ecliptic(ra: float, dec: float) -> tuple[float, float]:
    """Ecliptic longitude and latitude (degrees, J2000 mean obliquity)."""
    a, d = math.radians(ra), math.radians(dec)
    sb = math.sin(d) * math.cos(OBLIQUITY) - math.cos(d) * math.sin(OBLIQUITY) * math.sin(a)
    beta = math.asin(max(-1.0, min(1.0, sb)))
    lam = math.atan2(math.sin(a) * math.cos(OBLIQUITY) + math.tan(d) * math.sin(OBLIQUITY), math.cos(a))
    return math.degrees(lam) % 360.0, math.degrees(beta)


def all_cells() -> Iterator[Cell]:
    for b in range(N_BANDS):
        for c in range(_COLS[b]):
            yield cell(b, c)


def priority(ce: Cell) -> tuple[int, float]:
    """The sweep order key (lower first): 0 the Planet Nine band, 1 the rest of the ecliptic, 2 everything else."""
    lam, beta = ecliptic(ce.ra, ce.dec)
    if P9_RA[0] <= ce.ra <= P9_RA[1] and P9_BETA[0] <= beta <= P9_BETA[1]:
        return 0, sep_deg(ce.ra, ce.dec, *P9_CENTRE)
    lam0, _ = ecliptic(*P9_CENTRE)
    if abs(beta) <= ECLIPTIC_BAND_DEG:
        dl = abs((lam - lam0 + 180.0) % 360.0 - 180.0)
        return 1, dl + abs(beta) / 100.0
    return 2, abs(beta) * 1000.0 + lam


def sweep_order(tiers: tuple[int, ...] = (0, 1, 2)) -> Iterator[Cell]:
    """Every square in the sweep order. Tiers 0 and 1 are sorted in full (about 20 000 squares); tier 2 (the rest) is walked band by
    band from the ecliptic outwards, so the whole order never has to be held in memory."""
    if 0 in tiers or 1 in tiers:
        first: list[tuple[tuple[int, float], Cell]] = []
        for b in range(N_BANDS):
            dec_mid = -90.0 + (b + 0.5) * CELL_DEG
            if abs(dec_mid) > 23.44 + max(ECLIPTIC_BAND_DEG, -P9_BETA[0]) + 1.0:
                continue
            for c in range(_COLS[b]):
                ce = cell(b, c)
                p = priority(ce)
                if p[0] in tiers and p[0] < 2:
                    first.append((p, ce))
        first.sort(key=lambda t: t[0])
        for _, ce in first:
            yield ce
    if 2 in tiers:
        rest = sorted(range(N_BANDS), key=lambda b: abs(-90.0 + (b + 0.5) * CELL_DEG))
        for b in rest:
            for c in range(_COLS[b]):
                ce = cell(b, c)
                if priority(ce)[0] == 2:
                    yield ce


# ---------------------------------------------------------------------------------------------------------------------------------
# The product database: square states and human views.

SCHEMA = """
CREATE TABLE IF NOT EXISTS grid_cells (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  finds INTEGER NOT NULL DEFAULT 0,
  find_ids TEXT NOT NULL DEFAULT '[]',
  bar TEXT,
  field_key TEXT,
  searched_at TEXT,
  source TEXT,
  error TEXT,
  seconds REAL,
  peak_rss_mb REAL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS grid_cells_state ON grid_cells(state);
CREATE TABLE IF NOT EXISTS grid_views (
  cell_id TEXT NOT NULL,
  viewer TEXT NOT NULL,
  result TEXT,
  at REAL NOT NULL,
  PRIMARY KEY (cell_id, viewer)
);
CREATE TABLE IF NOT EXISTS grid_sweep_log (
  at REAL NOT NULL,
  cell_id TEXT,
  event TEXT NOT NULL,
  detail TEXT
);
"""


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class GridDB:
    """Square states and viewers in the product SQLite file (WAL; the sweep process and the API may both write)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c: sqlite3.Connection | None = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.c = c
        return c

    def get(self, cid: str) -> dict[str, Any] | None:
        r = self._conn().execute("SELECT * FROM grid_cells WHERE id=?", (cid,)).fetchone()
        return None if r is None else dict(r)

    def rows(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            q = f"SELECT * FROM grid_cells WHERE id IN ({','.join('?' * len(part))})"
            out.update({r["id"]: dict(r) for r in self._conn().execute(q, part)})
        return out

    def viewers(self, ids: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            q = f"SELECT cell_id, COUNT(*) n FROM grid_views WHERE cell_id IN ({','.join('?' * len(part))}) GROUP BY cell_id"
            out.update({r["cell_id"]: int(r["n"]) for r in self._conn().execute(q, part)})
        return out

    def put(self, cid: str, state: str, **kw: Any) -> None:
        if state not in STATES:
            raise ValueError(state)
        cur = self.get(cid) or {}
        row = {
            "finds": kw.get("finds", cur.get("finds", 0)),
            "find_ids": json.dumps(kw["find_ids"]) if "find_ids" in kw else cur.get("find_ids", "[]"),
            "bar": json.dumps(kw["bar"]) if "bar" in kw else cur.get("bar"),
            "field_key": kw.get("field_key", cur.get("field_key")),
            "searched_at": kw.get("searched_at", cur.get("searched_at")),
            "source": kw.get("source", cur.get("source")),
            "error": kw.get("error"),
            "seconds": kw.get("seconds", cur.get("seconds")),
            "peak_rss_mb": kw.get("peak_rss_mb", cur.get("peak_rss_mb")),
        }
        self._conn().execute(
            "INSERT INTO grid_cells(id,state,finds,find_ids,bar,field_key,searched_at,source,error,seconds,peak_rss_mb,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, finds=excluded.finds,"
            " find_ids=excluded.find_ids, bar=excluded.bar, field_key=excluded.field_key, searched_at=excluded.searched_at,"
            " source=excluded.source, error=excluded.error, seconds=excluded.seconds, peak_rss_mb=excluded.peak_rss_mb,"
            " updated=excluded.updated",
            (cid, state, *row.values(), time.time()),
        )

    def view(self, cid: str, viewer: str, result: str | None) -> int:
        self._conn().execute(
            "INSERT INTO grid_views(cell_id,viewer,result,at) VALUES(?,?,?,?) ON CONFLICT(cell_id,viewer) DO UPDATE SET"
            " result=COALESCE(excluded.result, grid_views.result), at=excluded.at",
            (cid, viewer, result, time.time()),
        )
        return self.viewers([cid]).get(cid, 0)

    def coverage(self, corners: bool = False) -> list[dict[str, Any]]:
        """Every square that is not `new`: a row in grid_cells or at least one viewer."""
        c = self._conn()
        rows = {r["id"]: dict(r) for r in c.execute("SELECT id, state, finds FROM grid_cells")}
        views = {r["cell_id"]: int(r["n"]) for r in c.execute("SELECT cell_id, COUNT(*) n FROM grid_views GROUP BY cell_id")}
        out = []
        for cid in sorted(set(rows) | set(views)):
            r = rows.get(cid, {"state": "new", "finds": 0})
            item: dict[str, Any] = {"id": cid, "state": r["state"], "finds": int(r["finds"]), "viewers": views.get(cid, 0)}
            if corners:
                try:
                    item["corners"] = by_id(cid).corners()
                except ValueError:
                    continue
            out.append(item)
        return out

    def counts(self) -> dict[str, int]:
        c = self._conn()
        searched = c.execute("SELECT COUNT(*) FROM grid_cells WHERE state='searched'").fetchone()[0]
        finds = c.execute("SELECT COUNT(*) FROM grid_cells WHERE finds>0").fetchone()[0]
        viewed = c.execute("SELECT COUNT(DISTINCT cell_id) FROM grid_views").fetchone()[0]
        by_state = {r[0]: int(r[1]) for r in c.execute("SELECT state, COUNT(*) FROM grid_cells GROUP BY state")}
        return {
            "searched": int(searched),
            "viewed": int(viewed),
            "with_finds": int(finds),
            **{f"state_{k}": v for k, v in by_state.items()},
        }

    def log(self, event: str, cell_id: str | None = None, **detail: Any) -> None:
        self._conn().execute(
            "INSERT INTO grid_sweep_log(at,cell_id,event,detail) VALUES(?,?,?,?)", (time.time(), cell_id, event, json.dumps(detail))
        )

    def rate_per_hour(self, window_s: float = 6 * 3600.0) -> float | None:
        """Squares the machine searched per hour over the last sweep window (from the sweep log: wall time of searched squares)."""
        c = self._conn()
        rows = c.execute("SELECT at FROM grid_sweep_log WHERE event='searched' AND at>? ORDER BY at", (time.time() - window_s,)).fetchall()
        if len(rows) < 2:
            secs = [r[0] for r in c.execute("SELECT seconds FROM grid_cells WHERE source='sweep' AND seconds>0")]
            return round(3600.0 / (sum(secs) / len(secs)), 2) if secs else None
        span = rows[-1][0] - rows[0][0]
        return round((len(rows) - 1) * 3600.0 / span, 2) if span > 0 else None


def scheme_params() -> dict[str, Any]:
    """The scheme in numbers, so a client can draw any square without its own copy of the rules."""
    return {
        "name": SCHEME,
        "cell_deg": CELL_DEG,
        "n_bands": N_BANDS,
        "dec0": -90.0,
        "cols": "max(1, round(360 * cos(dec_mid) / cell_deg)) equal RA steps from RA 0; dec_mid = -90 + (band + 0.5) * cell_deg",
        "id": "f'{band:03d}-{col:04d}'",
    }


def view_of(ce: Cell, row: dict[str, Any] | None, viewers: int) -> dict[str, Any]:
    row = row or {}
    bar = row.get("bar")
    return {
        "id": ce.id,
        "ra": round(ce.ra, 6),
        "dec": round(ce.dec, 6),
        "corners": ce.corners(),
        "state": row.get("state", "new"),
        "finds": int(row.get("finds") or 0),
        "viewers": viewers,
        "bar": json.loads(bar) if isinstance(bar, str) else bar,
        "searched_at": row.get("searched_at"),
    }


def import_searched(db: GridDB, fields: list[dict[str, Any]]) -> int:
    """Marks the squares of the places already searched (the list of /api/v1/searched): a finished place makes its square `searched`
    with the place's number of finds; a place smaller than the square says so in the bar (`partial`, the field size). A square the
    sweep already searched is left alone."""
    n = 0
    for f in fields:
        if f.get("state") != "done":
            continue
        ce = cell_at(float(f["ra"]), float(f["dec"]))
        cur = db.get(ce.id)
        if cur and cur.get("source") == "sweep" and cur.get("state") == "searched":
            continue
        prev = int(cur["finds"]) if cur and cur.get("source") == "import" else 0
        fov = float(f.get("fov") or 0)
        db.put(
            ce.id,
            "searched",
            finds=max(prev, int(f.get("found") or 0)),
            bar={"kind": "import", "searches": f.get("kinds"), "fov_arcmin": fov, "partial": fov < FIELD_FOV_ARCMIN},
            field_key=f.get("key"),
            searched_at=f.get("searched_at") or _iso(time.time()),
            source="import",
        )
        n += 1
    return n


def bar_at(db: GridDB, ra: float, dec: float) -> dict[str, Any] | None:
    """The bar the search of the square at (ra, dec) used (None while the square is not searched): for the `above_bar` check.
    Keys of a sweep bar: kind="sweep", mover_sigma, mover_shift_arcsec, passes, passes_used, sources_per_pass, chance_any,
    known_movers; of an imported place: kind="import", searches, fov_arcmin, partial."""
    row = db.get(cell_at(ra, dec).id)
    if not row or row.get("state") != "searched" or not row.get("bar"):
        return None
    bar: dict[str, Any] = json.loads(row["bar"])
    return bar
