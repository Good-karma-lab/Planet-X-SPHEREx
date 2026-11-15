# ruff: noqa: E501
"""Findings: every find of every searched field, kept in a database and identified against the catalogues.

The scans of a field (movers and pairs between passes, brown dwarfs, red sources, spectral anomalies, the film search, Find Planet X) each end in a
result file. `collect` reads them all and lists the finds; `Findings.sweep` writes them to `findings.db` (one row per find, the keys of its scan
products beside it) whenever a result file changed, so a scan that finishes is in the table within a minute and a field whose scans change is
refreshed. A small worker (`Findings.identify_loop`) asks every catalogue about every find: SIMBAD, Gaia DR3 (carried to the epoch with its proper
motion), 2MASS, AllWISE, VSX and NED at the place, and JPL's Small-Body Identification for the finds that move. One row per find and catalogue holds
what it said (`ok` with a name, separation and type, `none`, or `unavailable`: did not answer, asked again later). `/api/v1/findings` only reads.

The database is SQLite in WAL mode and shared by the two instances of a zero-downtime deploy: the schema is versioned (`meta.schema`), only the main
instance writes, and an instance that finds a newer schema than its own reads and leaves it alone.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx

from service.bodies import SB_IDENT, _iso, _sexa_dec, _sexa_ra
from service.products.store import Store
from service.products.versions import VERSIONS
from service.upstream.breaker import Breaker, BreakerOpenError

SCHEMA = 2
KINDS = ("mover", "browndwarf", "red", "anomaly", "pair", "film", "peak")
KIND_LABEL = {
    "mover": "Mover between passes",
    "browndwarf": "Brown dwarf",
    "red": "Red source",
    "anomaly": "Spectral anomaly",
    "pair": "Pair changing in place",
    "film": "Moving object in a pass (film search)",
    "peak": "Find Planet X candidate",
}
CATALOGUES = ("simbad", "gaia", "twomass", "allwise", "vsx", "ned")
CATALOGUE_LABEL = {
    "simbad": "SIMBAD",
    "gaia": "Gaia DR3",
    "twomass": "2MASS",
    "allwise": "AllWISE",
    "vsx": "VSX (variable stars)",
    "ned": "NED (galaxies)",
    "sbident": "JPL Small-Body Identification",
}
MOVING = (
    "film",
)  # asked of JPL Small-Body Identification: only tracks within one pass move like an asteroid (the movers between passes are stars)
SB_VMAG = 21.0  # SPHEREx depth: fainter bodies are not what a find could be
SB_MATCH_ARCSEC = 15.0
SB_RATE_TOL = 0.25
SB_PA_TOL = 30.0
GENERIC_TYPES = ("", "*", "Star", "IR", "Radio", "UV", "X", "Blue", "Red", "?")  # a SIMBAD type that explains nothing about a spectrum
EPOCH = 2025.9
RADIUS = 6.0  # arcsec: how far from a find a catalogue entry may stand and still be named
SWEEP_S = 45.0
RETRY_S = 90.0
MAX_ATTEMPTS = 8
RESULT_DIRS = ("movers", "pairs", "browndwarfs", "red", "anomalies")


def _cell(ra: float, dec: float) -> str:
    c = math.cos(math.radians(dec))
    return f"{round(ra * c * 3600 / 6)}:{round(dec * 3600 / 6)}"


def find_id(kind: str, ra: float, dec: float, extra: str = "") -> str:
    return f"{kind}:{hashlib.sha1(f'{kind}{_cell(ra, dec)}{extra}'.encode()).hexdigest()[:12]}"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        d: dict[str, Any] = json.loads(path.read_text())
        return d
    except (OSError, ValueError):
        return None


def place_name(targets: list[dict[str, Any]], ra: float, dec: float) -> str:
    """The test target whose field this is (within half a degree), else the coordinates."""
    best = min(targets, key=lambda t: math.hypot((t["ra"] - ra) * math.cos(math.radians(dec)), t["dec"] - dec), default=None)
    if best is not None and math.hypot((best["ra"] - ra) * math.cos(math.radians(dec)), best["dec"] - dec) < 0.5:
        return str(best["n"])
    return f"{ra:.2f} {dec:+.2f}"


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def collect(
    store_dir: Path, rows: list[dict[str, Any]], fkeys: dict[str, dict[str, str]], targets: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    """Every find in the result files, and a signature of those files (when it is unchanged nothing has to be written).
    `rows`: the product rows of the store; `fkeys[field_key]`: the keys of the field's result files by search."""
    finds: dict[str, dict[str, Any]] = {}
    sig = hashlib.sha1()
    fp: dict[str, float] | None = None  # the field being read: its place and size, to open its grid again

    def seen(p: Path) -> dict[str, Any] | None:
        try:
            s = p.stat()
        except OSError:
            return None
        sig.update(f"{p.name}{s.st_mtime_ns}{s.st_size}".encode())
        return _read(p)

    def add(f: dict[str, Any]) -> None:
        if fp is not None and f["kind"] in ("mover", "pair", "browndwarf", "red", "anomaly"):
            f["data"]["field_params"] = dict(fp)
        f.update(extras(f))
        cur = finds.get(f["id"])
        if cur is None or f.get("fov", 0) > cur.get("fov", 0):
            if cur is not None:
                f["scans"] = sorted(set(f["scans"]) | set(cur["scans"]))
            finds[f["id"]] = f
        else:
            cur["scans"] = sorted(set(cur["scans"]) | set(f["scans"]))

    for row in rows:
        p = row["params"]
        if row["kind"] == "field" and row["complete"]:
            k = fkeys[row["key"]]
            ra, dec, fov = float(p["ra"]), float(p["dec"]), float(p.get("fov", 5))
            place = place_name(targets, ra, dec)
            fp = {"ra": ra, "dec": dec, "fov": fov}
            scans = {"field": row["key"]}
            mv = seen(Path(store_dir) / "movers" / f"{k['movers']}.json")
            if mv:
                scans["movers"] = k["movers"]
                for t in mv.get("tracks", []):
                    if t.get("check") == "still":
                        continue
                    tr_ra, tr_dec = _f(t.get("ra_2025_9")), _f(t.get("dec_2025_9"))
                    if tr_ra is None or tr_dec is None:
                        continue
                    snr = [s for s in (t.get("snr") or []) if s is not None]
                    add(
                        {
                            "id": find_id("mover", tr_ra, tr_dec),
                            "kind": "mover",
                            "place": place,
                            "fov": fov,
                            "ra": tr_ra,
                            "dec": tr_dec,
                            "rate": _f(t.get("speed")),
                            "snr": min(snr) if snr else None,
                            "score": _f(t.get("sigma")),
                            "chance": _f((mv.get("chance") or {}).get("mean")),
                            "data": {
                                "track": t,
                                "passes": mv.get("passes"),
                                "mjd": mv.get("mjd"),
                                "chance": mv.get("chance"),
                                "field": mv.get("field"),
                            },
                            "scans": [v for v in (scans["field"], k["movers"]) if v],
                        }
                    )
                for pr in mv.get("pairs", []):
                    comps = pr.get("components") or []
                    if len(comps) < 2:
                        continue
                    pra = sum(c["ra"] for c in comps) / len(comps)
                    pdec = sum(c["dec"] for c in comps) / len(comps)
                    add(
                        {
                            "id": find_id("pair", pra, pdec),
                            "kind": "pair",
                            "place": place,
                            "fov": fov,
                            "ra": pra,
                            "dec": pdec,
                            "rate": None,
                            "snr": min((c.get("snr") or 0) for c in comps) or None,
                            "score": _f(pr.get("change_sigma")),
                            "chance": _f((mv.get("chance_pairs") or {}).get("expected")),
                            "data": {
                                "pair": {k2: v for k2, v in pr.items() if k2 != "crops"},
                                "field": mv.get("field"),
                                "mjd": mv.get("mjd"),
                            },
                            "scans": [scans["field"], k["movers"]],
                        }
                    )
            bd = seen(Path(store_dir) / "browndwarfs" / f"{k['browndwarfs']}.json")
            if bd:
                for group, known in (("candidates", False), ("known", True), ("slow", False)):
                    for c in bd.get(group, []):
                        cra, cdec = _f(c.get("ra")), _f(c.get("dec"))
                        if cra is None or cdec is None:
                            continue
                        add(
                            {
                                "id": find_id("browndwarf", cra, cdec),
                                "kind": "browndwarf",
                                "place": place,
                                "fov": fov,
                                "ra": cra,
                                "dec": cdec,
                                "rate": _f(c.get("pm")),
                                "snr": _f(c.get("snr_long")),
                                "score": _f(c.get("sigma")),
                                "chance": None,
                                "data": {
                                    "source": c,
                                    "group": "known" if known else ("slow" if group == "slow" else "candidate"),
                                    "mjd": bd.get("mjd"),
                                    "field": bd.get("field"),
                                },
                                "scans": [scans["field"], k["browndwarfs"]],
                            }
                        )
            rd = seen(Path(store_dir) / "red" / f"{_red_key(row['key'])}.json")
            if rd:
                for c in rd.get("red", []):
                    cra, cdec = _f(c.get("ra")), _f(c.get("dec"))
                    if cra is None or cdec is None:
                        continue
                    add(
                        {
                            "id": find_id("red", cra, cdec),
                            "kind": "red",
                            "place": place,
                            "fov": fov,
                            "ra": cra,
                            "dec": cdec,
                            "rate": None,
                            "snr": _f(c.get("snr_long")),
                            "score": _f(c.get("colour")),
                            "chance": None,
                            "data": {
                                "source": c,
                                "median_colour": rd.get("median_colour"),
                                "bands": rd.get("bands"),
                                "field": rd.get("field"),
                            },
                            "scans": [scans["field"], _red_key(row["key"])],
                        }
                    )
            an = seen(Path(store_dir) / "anomalies" / f"{k['anomalies']}.json")
            if an:
                for a in an.get("anomalies", []):
                    ara, adec = _f(a.get("ra")), _f(a.get("dec"))
                    if ara is None or adec is None:
                        continue
                    add(
                        {
                            "id": find_id("anomaly", ara, adec),
                            "kind": "anomaly",
                            "place": place,
                            "fov": fov,
                            "ra": ara,
                            "dec": adec,
                            "rate": None,
                            "snr": _f(a.get("snr")),
                            "score": _f(a.get("score")),
                            "chance": None,
                            "data": {
                                "anomaly": a,
                                "threshold": an.get("threshold"),
                                "edges": an.get("edges"),
                                "median": an.get("median"),
                                "field": an.get("field"),
                            },
                            "scans": [scans["field"], k["anomalies"]],
                        }
                    )
        elif row["kind"] == "filmsearch" and row["complete"]:
            meta = seen(Path(store_dir) / "products" / "filmsearch" / VERSIONS["filmsearch"] / row["key"][:2] / row["key"] / "meta.json")
            if not meta:
                continue
            g = meta.get("grid") or {}
            fr = meta.get("frames") or []
            place = place_name(targets, float(p["ra"]), float(p["dec"]))
            for i, t in enumerate(meta.get("tracks", [])):
                pts = t.get("pts") or []
                if not pts or not g:
                    continue
                mid = pts[len(pts) // 2]
                n, step = g["n"], g["step_arcsec"] / 3600.0
                dec = g["dec0"] + (n / 2 - mid["y"]) * step
                ra = g["ra0"] - (mid["x"] - n / 2) * step / math.cos(math.radians(g["dec0"]))
                mjd = fr[mid["f"]]["mjd"] if mid["f"] < len(fr) else None
                add(
                    {
                        "id": find_id("film", ra, dec, str(i)),
                        "kind": "film",
                        "place": place,
                        "fov": float(p.get("fov", 60)),
                        "ra": ra,
                        "dec": dec,
                        "rate": _f(t.get("rate")),
                        "snr": _f(t.get("z_together")),
                        "score": _f(t.get("z_together")),
                        "chance": None,
                        "data": {"track": t, "mjd": mjd, "film": p.get("mjd"), "chance": meta.get("chance") or meta.get("control")},
                        "scans": [row["key"]],
                    }
                )
        elif row["kind"] in ("distant", "deep") and row["complete"]:
            meta = seen(Path(store_dir) / "products" / row["kind"] / VERSIONS[row["kind"]] / row["key"][:2] / row["key"] / "meta.json")
            if not meta:
                continue
            c0 = (p.get("fields") or [[p.get("ra"), p.get("dec")]])[0] if row["kind"] == "deep" else [p.get("ra"), p.get("dec")]
            place = place_name(targets, float(c0[0]), float(c0[1])) if c0 and c0[0] is not None else "?"
            for c in (q for q in meta.get("candidates", []) if q.get("ok")):
                cra, cdec = _f(c.get("ra")), _f(c.get("dec"))
                if cra is None or cdec is None:
                    continue
                add(
                    {
                        "id": find_id("peak", cra, cdec, row["kind"]),
                        "kind": "peak",
                        "place": place,
                        "fov": float(p.get("fov", 18)),
                        "ra": cra,
                        "dec": cdec,
                        "rate": None,
                        "snr": _f(c.get("phot_sn") or c.get("sn_tuned") or c.get("sn")),
                        "score": _f(c.get("sn")),
                        "chance": None,
                        "data": {"peak": c, "search": row["kind"], "control": meta.get("control")},
                        "scans": [row["key"]],
                    }
                )
    return list(finds.values()), sig.hexdigest()


_PRODUCT = {
    "mover": "movers",
    "pair": "movers",
    "browndwarf": "browndwarfs",
    "red": "red",
    "anomaly": "anomalies",
    "film": "filmsearch",
    "peak": "distant",
}


def extras(f: dict[str, Any]) -> dict[str, Any]:
    """What a find carries beside its summary: its positions per pass, its spectrum, its light curves and the product version that produced it."""
    d, kind = f["data"], f["kind"]
    pos: list[dict[str, Any]] = []
    spec: list[dict[str, Any]] = []
    lc: list[dict[str, Any]] = []
    if kind == "mover":
        t = d.get("track") or {}
        snr = t.get("snr") or []
        for i, (m, ra, dec) in enumerate(zip(t.get("mjd") or [], t.get("ra") or [], t.get("dec") or [], strict=False)):
            pos.append(
                {
                    "label": f"pass {(t.get('passes') or list(range(99)))[i]}",
                    "mjd": m,
                    "ra": ra,
                    "dec": dec,
                    "snr": snr[i] if i < len(snr) else None,
                }
            )
    elif kind == "film":
        t = d.get("track") or {}
        for p in t.get("pts") or []:
            pos.append({"label": f"frame {p.get('f')} ({p.get('how')})", "mjd": None, "ra": None, "dec": None, "snr": p.get("z")})
    elif kind == "anomaly":
        a = d.get("anomaly") or {}
        edges = d.get("edges") or []
        shape, err, model = a.get("shape") or [], a.get("shape_err") or [], a.get("model") or []
        for i, v in enumerate(shape):
            lo = edges[i] if i < len(edges) else None
            hi = edges[i + 1] if i + 1 < len(edges) else None
            spec.append(
                {
                    "lam": lo,
                    "lam_hi": hi,
                    "flux": v,
                    "err": err[i] if i < len(err) else None,
                    "model": model[i] if i < len(model) else None,
                    "src": "binned spectrum, anomaly search",
                }
            )
    elif kind == "pair":
        mjds = d.get("mjd") or []
        for ci, c in enumerate((d.get("pair") or {}).get("components") or []):
            errs = c.get("flux_per_pass_err") or []
            for i, v in enumerate(c.get("flux_per_pass") or []):
                lc.append(
                    {
                        "label": f"component {ci + 1}",
                        "mjd": mjds[i] if i < len(mjds) else None,
                        "flux": v,
                        "err": errs[i] if i < len(errs) else None,
                    }
                )
    elif kind == "peak":
        for i, q in enumerate((d.get("peak") or {}).get("by_pass") or []):
            lc.append({"label": "flux per pass", "mjd": None, "flux": q.get("flux_mjy"), "err": None})
            pos.append({"label": f"pass {i}", "mjd": None, "ra": q.get("ra"), "dec": q.get("dec"), "snr": q.get("sn")})
    prod = _PRODUCT.get(kind, kind)
    return {"positions": pos, "spectrum": spec, "lightcurve": lc, "versions": {prod: VERSIONS.get(prod)}}


def _red_key(field_key: str) -> str:
    from service.products.store import product_key

    return product_key("red", {"field": field_key})


class FindingsDB:
    """findings.db: the finds, what each catalogue said about each, and the signature of the result files last read."""

    def __init__(self, path: Path, writer: bool) -> None:
        self.path, self.writer = path, writer
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=8000")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.read_only = False
        self.version = SCHEMA
        self._migrate()

    def _migrate(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        have = int(row[0]) if row else 0
        if have > SCHEMA:  # written by a newer instance: read what is there, change nothing
            self.read_only, self.version = True, have
            return
        if not self.writer:
            return
        if have < 1:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS finds (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, place TEXT, fov REAL, ra REAL NOT NULL, dec REAL NOT NULL, epoch REAL,
                    rate REAL, snr REAL, score REAL, chance REAL, data TEXT, scans TEXT, created REAL, updated REAL
                );
                CREATE INDEX IF NOT EXISTS finds_kind ON finds(kind);
                CREATE TABLE IF NOT EXISTS idents (
                    find_id TEXT NOT NULL, catalogue TEXT NOT NULL, state TEXT NOT NULL, name TEXT, sep REAL, type TEXT, detail TEXT,
                    tried REAL, attempts INTEGER DEFAULT 0, PRIMARY KEY (find_id, catalogue)
                );
                """
            )
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '1')")
            self.db.commit()
        if have < 2:
            # everything about a find in related tables; the product versions that produced the values; the catalogues' full answers
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(finds)")}
            if "versions" not in cols:
                self.db.execute("ALTER TABLE finds ADD COLUMN versions TEXT")
            icols = {r[1] for r in self.db.execute("PRAGMA table_info(idents)")}
            if "raw" not in icols:
                self.db.execute("ALTER TABLE idents ADD COLUMN raw TEXT")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (find_id TEXT NOT NULL, idx INTEGER NOT NULL, label TEXT, mjd REAL, ra REAL, dec REAL, snr REAL,
                    PRIMARY KEY (find_id, idx));
                CREATE TABLE IF NOT EXISTS spectra (find_id TEXT NOT NULL, idx INTEGER NOT NULL, lam REAL, lam_hi REAL, flux REAL, err REAL, model REAL,
                    detector TEXT, mjd REAL, flag TEXT, src TEXT, PRIMARY KEY (find_id, idx));
                CREATE TABLE IF NOT EXISTS lightcurves (find_id TEXT NOT NULL, idx INTEGER NOT NULL, label TEXT, mjd REAL, flux REAL, err REAL,
                    PRIMARY KEY (find_id, label, idx));
                CREATE TABLE IF NOT EXISTS images (find_id TEXT NOT NULL, kind TEXT NOT NULL, pass INTEGER NOT NULL DEFAULT 0, path TEXT, sha1 TEXT,
                    created REAL, PRIMARY KEY (find_id, kind, pass));
                """
            )
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '2')")
            self.db.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()

    def write_finds(self, finds: list[dict[str, Any]]) -> tuple[int, int]:
        """Brings the table to this list: new finds in, changed ones updated (their identification kept), vanished ones out."""
        now = time.time()
        have = {r["id"] for r in self.db.execute("SELECT id FROM finds")}
        keep = set()
        for f in finds:
            keep.add(f["id"])
            self.db.execute(
                "INSERT INTO finds (id, kind, place, fov, ra, dec, epoch, rate, snr, score, chance, data, scans, created, updated, versions) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET versions=excluded.versions,  place=excluded.place, fov=excluded.fov, ra=excluded.ra, dec=excluded.dec, "
                "rate=excluded.rate, snr=excluded.snr, score=excluded.score, chance=excluded.chance, data=excluded.data, scans=excluded.scans, updated=excluded.updated",
                (
                    f["id"], f["kind"], f["place"], f["fov"], f["ra"], f["dec"], EPOCH, f["rate"], f["snr"], f["score"], f["chance"],
                    json.dumps(f["data"], separators=(",", ":")), json.dumps(f["scans"]), now, now, json.dumps(f.get("versions") or {}),
                ),
            )  # fmt: skip
            self._write_related(f)
        gone = have - keep
        for i in gone:
            self.db.execute("DELETE FROM finds WHERE id=?", (i,))
            self.db.execute("DELETE FROM idents WHERE find_id=?", (i,))
        self.db.commit()
        return len(keep - have), len(gone)

    def _write_related(self, f: dict[str, Any]) -> None:
        """The find's positions, spectrum and light curves: replaced, never duplicated, when its scans were computed again."""
        i = f["id"]
        for t in ("positions", "spectra", "lightcurves"):
            self.db.execute(f"DELETE FROM {t} WHERE find_id=?", (i,))
        self.db.executemany(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
            [(i, k, p.get("label"), p.get("mjd"), p.get("ra"), p.get("dec"), p.get("snr")) for k, p in enumerate(f.get("positions") or [])],
        )
        self.db.executemany(
            "INSERT INTO spectra VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    i,
                    k,
                    p.get("lam"),
                    p.get("lam_hi"),
                    p.get("flux"),
                    p.get("err"),
                    p.get("model"),
                    p.get("detector"),
                    p.get("mjd"),
                    p.get("flag"),
                    p.get("src"),
                )
                for k, p in enumerate(f.get("spectrum") or [])
            ],
        )
        self.db.executemany(
            "INSERT INTO lightcurves VALUES (?,?,?,?,?,?)",
            [(i, k, p.get("label"), p.get("mjd"), p.get("flux"), p.get("err")) for k, p in enumerate(f.get("lightcurve") or [])],
        )

    def related(self, ids: list[str]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        out: dict[str, dict[str, list[dict[str, Any]]]] = {
            i: {"positions": [], "spectrum": [], "lightcurve": [], "images": []} for i in ids
        }
        for k in range(0, len(ids), 500):
            chunk = ids[k : k + 500]
            m = ",".join("?" for _ in chunk)
            for r in self.db.execute(f"SELECT * FROM positions WHERE find_id IN ({m}) ORDER BY idx", chunk):
                out[r["find_id"]]["positions"].append({c: r[c] for c in ("label", "mjd", "ra", "dec", "snr")})
            for r in self.db.execute(f"SELECT * FROM spectra WHERE find_id IN ({m}) ORDER BY idx", chunk):
                out[r["find_id"]]["spectrum"].append(
                    {c: r[c] for c in ("lam", "lam_hi", "flux", "err", "model", "detector", "mjd", "flag", "src")}
                )
            for r in self.db.execute(f"SELECT * FROM lightcurves WHERE find_id IN ({m}) ORDER BY label, idx", chunk):
                out[r["find_id"]]["lightcurve"].append({c: r[c] for c in ("label", "mjd", "flux", "err")})
            for r in self.db.execute(f"SELECT * FROM images WHERE find_id IN ({m}) ORDER BY kind, pass", chunk):
                out[r["find_id"]]["images"].append({c: r[c] for c in ("kind", "pass", "path", "sha1")})
        return out

    def pending(self, limit: int) -> list[sqlite3.Row]:
        """Finds with a catalogue not yet answered (or that did not answer and may be asked again): the oldest asked first."""
        now = time.time()
        rows = self.db.execute("SELECT id, kind, ra, dec, data FROM finds").fetchall()
        out: list[tuple[float, sqlite3.Row]] = []
        done = {(r["find_id"], r["catalogue"]): r for r in self.db.execute("SELECT * FROM idents")}
        for r in rows:
            want = list(CATALOGUES) + (["sbident"] if r["kind"] in MOVING else [])
            worst = None
            for c in want:
                d = done.get((r["id"], c))
                if d is None:
                    worst = 0.0 if worst is None else min(worst, 0.0)
                elif (
                    d["state"] == "unavailable"
                    and d["attempts"] < MAX_ATTEMPTS
                    and now - d["tried"] >= RETRY_S * (2 ** min(d["attempts"], 5))
                ):
                    worst = d["tried"] if worst is None else min(worst, d["tried"])
            if worst is not None:
                out.append((worst, r))
        out.sort(key=lambda x: x[0])
        return [r for _, r in out[:limit]]

    def put_ident(
        self,
        find_id: str,
        catalogue: str,
        state: str,
        name: str | None,
        sep: float | None,
        typ: str | None,
        detail: dict[str, Any],
        raw: dict[str, Any] | None = None,
    ) -> None:
        row = self.db.execute("SELECT attempts FROM idents WHERE find_id=? AND catalogue=?", (find_id, catalogue)).fetchone()
        attempts = (int(row[0]) if row else 0) + (1 if state == "unavailable" else 0)
        self.db.execute(
            "INSERT OR REPLACE INTO idents (find_id, catalogue, state, name, sep, type, detail, tried, attempts, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                find_id,
                catalogue,
                state,
                name,
                sep,
                typ,
                json.dumps(detail, separators=(",", ":")),
                time.time(),
                attempts,
                json.dumps(raw, separators=(",", ":")) if raw is not None else None,
            ),
        )

    def idents_of(self, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
        for k in range(0, len(ids), 500):
            chunk = ids[k : k + 500]
            marks = ",".join("?" for _ in chunk)
            for r in self.db.execute(f"SELECT * FROM idents WHERE find_id IN ({marks})", chunk):
                out[r["find_id"]].append(
                    {
                        "catalogue": r["catalogue"],
                        "label": CATALOGUE_LABEL.get(r["catalogue"], r["catalogue"]),
                        "state": r["state"],
                        "name": r["name"],
                        "sep": r["sep"],
                        "type": r["type"],
                        "detail": json.loads(r["detail"] or "{}"),
                        "raw": json.loads(r["raw"]) if r["raw"] else None,
                        "tried": r["tried"],
                        "attempts": r["attempts"],
                    }
                )
        return out


def _ident_from(cat: str, ans: dict[str, Any]) -> tuple[str, str | None, float | None, str | None, dict[str, Any]]:
    """(state, name, separation, type, detail) of one catalogue's answer from `CatalogueContext.context`."""
    st = ans.get("state")
    if st in ("unavailable", "pending") or st is None:
        return "unavailable", None, None, None, {"error": ans.get("error")}
    if st == "none":
        return "none", None, None, None, {}
    if cat == "simbad":
        return "ok", ans.get("name"), ans.get("sep"), ans.get("otype"), {"sptype": ans.get("sptype"), "link": ans.get("link")}
    if cat == "gaia":
        gid = ans.get("source_id")
        return "ok", f"Gaia DR3 {gid}", ans.get("sep"), None, {k: ans.get(k) for k in ("g", "bp_rp", "parallax", "pmra", "pmdec", "ruwe")}
    if cat in ("twomass", "allwise"):
        return (
            "ok",
            ans.get("designation"),
            ans.get("sep"),
            None,
            {k: v for k, v in ans.items() if k not in ("state", "designation", "sep")},
        )
    return (
        "ok",
        ans.get("name"),
        ans.get("sep"),
        ans.get("type"),
        {k: v for k, v in ans.items() if k not in ("state", "name", "type", "sep")},
    )


class Findings:
    def __init__(
        self, store: Store, store_dir: Path, catalogue: Any, movers: Any, pairs: Any, dwarfs: Any, anomalies: Any, writer: bool = True
    ) -> None:
        self.store, self.store_dir, self.catalogue = store, store_dir, catalogue
        self.movers, self.pairs, self.dwarfs, self.anomalies = movers, pairs, dwarfs, anomalies
        self.db = FindingsDB(store_dir / "findings.db", writer)
        self.writer = writer and not self.db.read_only
        self.breaker = Breaker("JPL Small-Body Identification")
        self.tasks: list[asyncio.Task[None]] = []
        self.lock = asyncio.Lock()
        self.kick = asyncio.Event()
        try:
            self.targets = [
                {"n": o["n"], "ra": o["ra"], "dec": o["dec"]}
                for o in json.loads((Path(__file__).resolve().parent.parent / "data" / "catalog.json").read_text())["catalog"]
                if o.get("b")
            ]
        except (OSError, ValueError, KeyError):
            self.targets = []

    def start(self) -> None:
        if self.writer:
            self.tasks = [asyncio.create_task(self.sweep_loop()), asyncio.create_task(self.identify_loop())]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()

    def notify(self) -> None:
        """A scan finished (hook for the scan workers): sweep now instead of at the next tick."""
        self.kick.set()

    async def sweep(self) -> dict[str, int]:
        rows = self.store.listing(("field", "filmsearch", "distant", "deep"))
        fkeys = {
            r["key"]: {
                "movers": self.movers.key_of(r["key"]),
                "pairs": self.pairs.key_of(r["key"]),
                "browndwarfs": self.dwarfs.key_of(r["key"]),
                "anomalies": self.anomalies.key_of(r["key"]),
            }
            for r in rows
            if r["kind"] == "field"
        }
        finds, sig = await asyncio.to_thread(collect, self.store_dir, rows, fkeys, self.targets)
        if sig == self.db.get_meta("sig"):
            return {"added": 0, "removed": 0, "finds": len(finds)}
        async with self.lock:
            added, removed = await asyncio.to_thread(self.db.write_finds, finds)
            self.db.set_meta("sig", sig)
        return {"added": added, "removed": removed, "finds": len(finds)}

    async def sweep_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.sweep()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.kick.wait(), SWEEP_S)
            self.kick.clear()

    async def sbident(self, ra: float, dec: float, mjd: float, rate: float | None, pa: float | None) -> dict[str, Any]:
        """JPL's Small-Body Identification at the place and time of a moving find (the position at that very time). A body counts only if it
        stands within 15″, is brighter than V 21, and moves at the find's rate (25 %) and direction (30°); the nearest such body is named with
        both rates. When bodies stand near but none moves like the find, the answer is `none` and says what the nearest was."""
        half = 60.0 / 3600.0
        self.breaker.before()
        try:
            r = await self.catalogue.client.get(
                SB_IDENT,
                params={
                    "mpc-code": "500",
                    "obs-time": _iso(mjd),
                    "fov-ra-center": _sexa_ra(ra),
                    "fov-dec-center": _sexa_dec(dec),
                    "fov-ra-hwidth": f"{half / max(0.05, math.cos(math.radians(dec))):.5f}",
                    "fov-dec-hwidth": f"{half:.5f}",
                    "vmag-lim": str(SB_VMAG),
                },
            )
            r.raise_for_status()
            body: dict[str, Any] = r.json()
        except Exception:
            self.breaker.failure()
            raise
        self.breaker.success()
        fields = body.get("fields_first") or body.get("fields") or []
        data = body.get("data_first_pass") or body.get("data") or []
        idx = {n: i for i, n in enumerate(fields)}
        c_ra, c_dec = 'Dist. from center RA (")', 'Dist. from center Dec (")'
        r_ra, r_dec, c_v = 'RA rate ("/h)', 'Dec rate ("/h)', "Visual magnitude (V)"
        near: list[tuple[float, list[Any]]] = []
        for row in data:
            try:
                near.append((math.hypot(float(row[idx[c_ra]]), float(row[idx[c_dec]])), row))
            except (KeyError, ValueError, IndexError):
                continue
        near.sort(key=lambda x: x[0])
        out: dict[str, Any] = {"n_bodies": len(data), "vmag_limit": SB_VMAG}
        for sep, row in near:
            if sep > SB_MATCH_ARCSEC:
                break
            try:
                vr, vd = float(row[idx[r_ra]]), float(row[idx[r_dec]])
            except (KeyError, ValueError, IndexError):
                out.update(state="none", note=f"{row[0]} stands {sep:.1f}″ away but JPL gave no rate to compare")
                continue
            speed, ang = math.hypot(vr, vd), math.degrees(math.atan2(vr, vd)) % 360.0
            agrees = (
                rate is not None
                and pa is not None
                and abs(speed - rate) <= SB_RATE_TOL * rate
                and abs((ang - pa + 180.0) % 360.0 - 180.0) <= SB_PA_TOL
            )
            cand = {
                "name": str(row[0]),
                "sep": round(sep, 1),
                "v": row[idx[c_v]] if c_v in idx else None,
                "body_rate": round(speed, 1),
                "body_pa": round(ang),
                "our_rate": rate,
                "our_pa": pa,
            }
            if agrees:
                return {"state": "ok", **out, **cand}
            out.update(
                state="none",
                nearest_rejected=cand,
                note=f"{cand['name']} stands {sep:.1f}″ away but moves {speed:.0f}″/h toward {ang:.0f}°, not like this find",
            )
        out.setdefault("state", "none")
        return out

    def _mjd_of(self, row: sqlite3.Row) -> float | None:
        d = json.loads(row["data"])
        m = d.get("mjd")
        if isinstance(m, list):
            m = m[len(m) // 2] if m else None
        if m is None:
            m = (d.get("track") or {}).get("mjd")
            if isinstance(m, list):
                m = m[len(m) // 2] if m else None
        if m is None:
            m = d.get("film")
        return _fnum(m)

    async def identify_one(self, row: sqlite3.Row) -> None:
        ctx = await self.catalogue.context(row["ra"], row["dec"], EPOCH, RADIUS)
        for cat in CATALOGUES:
            a = ctx.get(cat) or {"state": "unavailable", "error": "no answer"}
            state, name, sep, typ, detail = _ident_from(cat, a)
            self.db.put_ident(row["id"], cat, state, name, sep, typ, detail, a)
        if row["kind"] in MOVING:
            mjd = self._mjd_of(row)
            if mjd is None:
                self.db.put_ident(row["id"], "sbident", "unavailable", None, None, None, {"error": "no observation time"})
            else:
                try:
                    trk = json.loads(row["data"]).get("track") or {}
                    a = await self.sbident(row["ra"], row["dec"], mjd, _fnum(trk.get("rate")), _fnum(trk.get("pa")))
                    self.db.put_ident(
                        row["id"],
                        "sbident",
                        a["state"],
                        a.get("name"),
                        a.get("sep"),
                        None,
                        {k: a[k] for k in ("v", "n_bodies") if k in a},
                        a,
                    )
                except (httpx.HTTPError, BreakerOpenError, ValueError) as e:
                    self.db.put_ident(row["id"], "sbident", "unavailable", None, None, None, {"error": str(e)[:120]})
        self.db.db.commit()

    async def identify_loop(self) -> None:
        while True:
            try:
                batch = await asyncio.to_thread(self.db.pending, 6)
                if not batch:
                    await asyncio.sleep(15)
                    continue
                for row in batch:
                    await self.identify_one(row)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(10)


def _fnum(v: Any) -> float | None:
    return _f(v)


# ---- reading -------------------------------------------------------------------------------------------------------------------------------------


def status_of(f: dict[str, Any], idents: list[dict[str, Any]]) -> str:
    """`known` (a catalogue names what it is), `unexplained` (the catalogues answered and none does, or not yet asked) per kind."""
    by = {i["catalogue"]: i for i in idents}

    def ok(c: str) -> bool:
        return by.get(c, {}).get("state") == "ok"

    d = f["data"]
    kind = f["kind"]
    if kind == "mover":
        t = d.get("track") or {}
        if t.get("check") == "same" or t.get("name") or str(t.get("status", "")).startswith("known"):
            return "known"
        return "unexplained"
    if kind == "browndwarf":
        return (
            "known"
            if d.get("group") == "known" or (ok("simbad") and by["simbad"].get("type", "") and "BD" in str(by["simbad"]["type"]))
            else "unexplained"
        )
    if kind == "film":
        return "known" if ok("sbident") else "unexplained"
    if kind in ("red", "anomaly"):
        # SIMBAD must say what it is: a bare "Star" or "IR" explains neither a red colour nor an odd spectrum
        return "known" if ok("simbad") and str(by["simbad"].get("type") or "") not in GENERIC_TYPES else "unexplained"
    if kind == "pair":
        return "known" if ok("vsx") else "unexplained"
    return "unexplained"


def verdict_of(f: dict[str, Any], idents: list[dict[str, Any]], status: str) -> str:
    """One plain sentence about what the catalogues say."""
    asked = [i for i in idents if i["state"] != "pending"]
    failed = [i["label"] for i in asked if i["state"] == "unavailable"]
    named = [
        f"{i['label']}: {i['name']}"
        + (f" ({i['type']})" if i.get("type") else "")
        + (f", {i['sep']:.1f}″ away" if i.get("sep") is not None else "")
        for i in idents
        if i["state"] == "ok"
    ]
    if not idents:
        return "The catalogues have not been asked yet."
    kind = f["kind"]
    if status == "known":
        who = "; ".join(named[:2]) if named else "a catalogue"
        base = {
            "mover": "A known object that moves like this: ",
            "browndwarf": "A known brown dwarf: ",
            "film": "A known solar-system body is predicted here: ",
            "red": "A catalogued source: ",
            "anomaly": "A catalogued source with an odd spectrum: ",
            "pair": "A catalogued variable: ",
        }.get(kind, "Known: ")
        text = base + who + "."
    else:
        none = [i["label"] for i in idents if i["state"] == "none"]
        text = {
            "mover": "It moves between the passes and no catalogue names it. A lead to look at, not a discovery.",
            "browndwarf": "A red mover no catalogue names: a brown dwarf candidate, not a confirmed one.",
            "film": "It moves within one pass and JPL lists no known body there. A lead, not a discovery.",
            "red": "Very red in the 4–5 µm bands and no SIMBAD source there.",
            "anomaly": "An odd spectrum and no SIMBAD source there.",
            "pair": "Two sources whose flux changes in place; no catalogue calls it a variable.",
            "peak": "A candidate of the Find Planet X search: a peak above the bar, not a detection.",
        }.get(kind, "No catalogue names it.")
        if none:
            text += f" Nothing within {RADIUS:.0f}″ in {', '.join(none)}."
    if failed:
        text += f" Did not answer: {', '.join(failed)}."
    return text


def query(db: FindingsDB, kind: str | None, status: str | None, place: str | None, limit: int, offset: int) -> dict[str, Any]:
    rows = db.db.execute("SELECT * FROM finds").fetchall()
    ids = [r["id"] for r in rows]
    idents = db.idents_of(ids)
    rel = db.related(ids)
    items: list[dict[str, Any]] = []
    for r in rows:
        f = {
            "id": r["id"], "kind": r["kind"], "place": r["place"], "fov": r["fov"], "ra": r["ra"], "dec": r["dec"], "epoch": r["epoch"],
            "rate": r["rate"], "snr": r["snr"], "score": r["score"], "chance": r["chance"],
            "data": json.loads(r["data"] or "{}"), "scans": json.loads(r["scans"] or "[]"), "updated": r["updated"],
            "versions": json.loads(r["versions"] or "{}"), **rel[r["id"]],
        }  # fmt: skip
        ids_ = idents[r["id"]]
        have = {i["catalogue"] for i in ids_}
        for c in list(CATALOGUES) + (["sbident"] if r["kind"] in MOVING else []):
            if c not in have:
                ids_.append(
                    {
                        "catalogue": c,
                        "label": CATALOGUE_LABEL[c],
                        "state": "pending",
                        "name": None,
                        "sep": None,
                        "type": None,
                        "detail": {},
                        "tried": None,
                        "attempts": 0,
                    }
                )
        order = {c: i for i, c in enumerate([*list(CATALOGUES), "sbident"])}
        ids_.sort(key=lambda i: order.get(i["catalogue"], 99))
        st = status_of(f, [i for i in ids_ if i["state"] != "pending"])
        f["status"] = st
        f["idents"] = ids_
        f["verdict"] = verdict_of(f, [i for i in ids_ if i["state"] != "pending"], st)
        items.append(f)
    counts: dict[str, dict[str, int]] = {k: {"all": 0, "unexplained": 0, "known": 0} for k in KINDS}
    for f in items:
        tally = counts.setdefault(f["kind"], {"all": 0, "unexplained": 0, "known": 0})
        tally["all"] += 1
        tally[f["status"]] += 1
    sel = [
        f
        for f in items
        if (not kind or f["kind"] == kind)
        and (not status or status == "all" or f["status"] == status)
        and (not place or f["place"] == place)
    ]
    rank = {"unexplained": 0, "known": 1}
    sel.sort(key=lambda f: (rank[f["status"]], KINDS.index(f["kind"]) if f["kind"] in KINDS else 99, -(f["score"] or f["snr"] or 0.0)))
    pending = sum(1 for f in items for i in f["idents"] if i["state"] == "pending")
    unavailable = sum(1 for f in items for i in f["idents"] if i["state"] == "unavailable")
    return {
        "count": len(sel),
        "total": len(items),
        "counts": counts,
        "labels": KIND_LABEL,
        "identification": {
            "pending": pending,
            "unavailable": unavailable,
            "catalogues": [CATALOGUE_LABEL[c] for c in CATALOGUES] + [CATALOGUE_LABEL["sbident"]],
        },
        "finds": sel[offset : offset + limit],
    }
