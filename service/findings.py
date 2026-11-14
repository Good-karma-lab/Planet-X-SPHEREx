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

SCHEMA = 1
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
MOVING = ("mover", "browndwarf", "film", "peak")  # asked of JPL Small-Body Identification too
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

    def seen(p: Path) -> dict[str, Any] | None:
        try:
            s = p.stat()
        except OSError:
            return None
        sig.update(f"{p.name}{s.st_mtime_ns}{s.st_size}".encode())
        return _read(p)

    def add(f: dict[str, Any]) -> None:
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
                "INSERT INTO finds (id, kind, place, fov, ra, dec, epoch, rate, snr, score, chance, data, scans, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET place=excluded.place, fov=excluded.fov, ra=excluded.ra, dec=excluded.dec, "
                "rate=excluded.rate, snr=excluded.snr, score=excluded.score, chance=excluded.chance, data=excluded.data, scans=excluded.scans, updated=excluded.updated",
                (
                    f["id"], f["kind"], f["place"], f["fov"], f["ra"], f["dec"], EPOCH, f["rate"], f["snr"], f["score"], f["chance"],
                    json.dumps(f["data"], separators=(",", ":")), json.dumps(f["scans"]), now, now,
                ),
            )  # fmt: skip
        gone = have - keep
        for i in gone:
            self.db.execute("DELETE FROM finds WHERE id=?", (i,))
            self.db.execute("DELETE FROM idents WHERE find_id=?", (i,))
        self.db.commit()
        return len(keep - have), len(gone)

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
        self, find_id: str, catalogue: str, state: str, name: str | None, sep: float | None, typ: str | None, detail: dict[str, Any]
    ) -> None:
        row = self.db.execute("SELECT attempts FROM idents WHERE find_id=? AND catalogue=?", (find_id, catalogue)).fetchone()
        attempts = (int(row[0]) if row else 0) + (1 if state == "unavailable" else 0)
        self.db.execute(
            "INSERT OR REPLACE INTO idents VALUES (?,?,?,?,?,?,?,?,?)",
            (find_id, catalogue, state, name, sep, typ, json.dumps(detail, separators=(",", ":")), time.time(), attempts),
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
                        "tried": r["tried"],
                        "attempts": r["attempts"],
                    }
                )
        return out


def _ident_from(cat: str, ans: dict[str, Any]) -> tuple[str, str | None, float | None, str | None, dict[str, Any]]:
    """(state, name, separation, type, detail) of one catalogue's answer from `CatalogueContext.context`."""
    st = ans.get("state")
    if st == "unavailable" or st is None:
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

    async def sbident(self, ra: float, dec: float, mjd: float) -> dict[str, Any]:
        """JPL's Small-Body Identification at the place and time of a moving find: the nearest known body within 60″, or none."""
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
                    "vmag-lim": "26",
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
        if not data:
            return {"state": "none"}
        idx = {n: i for i, n in enumerate(fields)}
        best: tuple[float, list[Any]] | None = None
        for row in data:
            try:
                d = math.hypot(float(row[idx['Dist. from center RA (")']]), float(row[idx['Dist. from center Dec (")']]))
            except (KeyError, ValueError, IndexError):
                d = 0.0
            if best is None or d < best[0]:
                best = (d, row)
        assert best is not None
        row = best[1]
        v = row[idx["Visual magnitude (V)"]] if "Visual magnitude (V)" in idx else None
        return {"state": "ok", "name": str(row[0]), "sep": round(best[0], 1), "v": v, "n_bodies": len(data)}

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
            self.db.put_ident(row["id"], cat, state, name, sep, typ, detail)
        if row["kind"] in MOVING:
            mjd = self._mjd_of(row)
            if mjd is None:
                self.db.put_ident(row["id"], "sbident", "unavailable", None, None, None, {"error": "no observation time"})
            else:
                try:
                    a = await self.sbident(row["ra"], row["dec"], mjd)
                    self.db.put_ident(
                        row["id"], "sbident", a["state"], a.get("name"), a.get("sep"), None, {k: a[k] for k in ("v", "n_bodies") if k in a}
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
        if t.get("check") == "same" or t.get("name") or str(t.get("status", "")).startswith("known") or ok("sbident"):
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
    if kind == "red":
        return "known" if ok("simbad") else "unexplained"
    if kind == "anomaly":
        return "known" if ok("simbad") else "unexplained"
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
    items: list[dict[str, Any]] = []
    for r in rows:
        f = {
            "id": r["id"], "kind": r["kind"], "place": r["place"], "fov": r["fov"], "ra": r["ra"], "dec": r["dec"], "epoch": r["epoch"],
            "rate": r["rate"], "snr": r["snr"], "score": r["score"], "chance": r["chance"],
            "data": json.loads(r["data"] or "{}"), "scans": json.loads(r["scans"] or "[]"), "updated": r["updated"],
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
