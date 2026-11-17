"""Known objects of a field (the lab's "Known objects" button and the catalogue checks before submit): every Gaia DR3 star, SIMBAD
object and JPL Solar System body inside a square frame, each with its place at every asked epoch.

    Gaia     one cone over the frame (ESA archive, then GAVO, then the Parquet copy on AWS): stars of G <= GAIA_MAG_LIMIT and every star
             that moves GAIA_FAST_KEEP_MAS or more a year; the place at 2016.0 carried to the epoch by proper motion and parallax
    SIMBAD   one cone (two mirrors); the place at 2000.0 carried the same way; an object that is a Gaia star (within MERGE_ARCSEC at
             2016.0) is not listed twice: the Gaia star takes its name and type
    JPL      Small-Body Identification at the time of each epoch (geocentric, V <= 20.5): the bodies in the frame at that time, with the
             place JPL gives; an epoch given as a year with one decimal is 36 days wide, so the caller gives the pass date (MJD)

For MATCHING only (never drawn: the `match` list beside `objects`), the frame is also asked of deeper catalogues, so that a
lab "no catalogue match" means unknown, not merely fainter than the drawn rings:
    Gaia     the stars of GAIA_MAG_LIMIT < G <= GAIA_MATCH_MAG (moved by proper motion as above)
    CatWISE2020  W1/W2 sources with their own proper motion (epoch 2015.4): the infrared stars Gaia does not see
    AllWISE  W1-W4 sources (epoch 2010.5, no motion)
    2MASS    J/H/Ks point sources (epoch 1999.5, no motion)
merged by position (MATCH_MERGE_ARCSEC at the first epoch; one entry lists every catalogue that has the source) and without the
sources already in `objects`. Each deep catalogue says its own state in `status.match`; one that did not answer is said, never taken
for "nothing there".

The catalogue rows are kept on disk per field (the propagation is done per request, in numpy); a catalogue that did not answer is said
in `answered` and `status`, never shown as an empty field. JPL allows one identification every 15 s: epochs not answered within
JPL_WAIT_S stay `pending`, are asked in the background and kept, so the next request has them.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import erfa  # type: ignore[import-untyped]
import httpx
import numpy as np
from scipy.spatial import cKDTree

from service import cpu
from service.context import GAIA_TAPS, SIMBAD_BASES, display_name, gaia_cloud_rows
from service.storage import digest
from service.upstream import jpl
from service.upstream.breaker import Breaker, BreakerOpenError

GAIA_EPOCH, SIMBAD_EPOCH = 2016.0, 2000.0
GAIA_MAG_LIMIT = 19.0  # fainter Gaia stars are not seen in one SPHEREx pass; a fast one is kept whatever its magnitude
GAIA_FAST_KEEP_MAS = 100.0
GAIA_MATCH_MAG = 21.0  # matching goes this deep in Gaia (the drawn rings stop at GAIA_MAG_LIMIT)
MATCH_MAX_ROWS = 30000  # per deep catalogue (said in `status.match.<cat>.truncated`)
MATCH_MERGE_ARCSEC = 2.5  # one source in several catalogues: WISE places are good to about an arcsecond, the pixel is 6″
MATCH_WAIT_S = 25.0  # a deep catalogue that has not answered by then is `pending` (asked on in the background, kept for the next ask)
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
# deep catalogue -> (IRSA table, columns, epoch, magnitude column, its band, proper motion unit in mas/yr or None)
IRSA_MATCH: dict[str, tuple[str, str, float, str, str, float | None]] = {
    "catwise": ("catwise_2020", "source_name AS id, ra, dec, w1mpro AS mag, pmra, pmdec", 2015.4, "mag", "W1", 1000.0),
    "allwise": ("allwise_p3as_psd", "designation AS id, ra, dec, w1mpro AS mag", 2010.5, "mag", "W1", None),
    "twomass": ("fp_psc", "designation AS id, ra, dec, k_m AS mag", 1999.5, "mag", "Ks", None),
}
MATCH_NAMES = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "allwise": "AllWISE", "twomass": "2MASS"}
GAIA_MAX_ROWS = 20000  # the brightest this many of a crowded field (said in `status.gaia.truncated`)
SIMBAD_MAX_ROWS = 5000
FAST_MAS = 200.0  # `label: true` at or above this total proper motion (mas/yr): 0.2″ a year, the stars that visibly shift over the mission
BRIGHT_MAG = 10.0  # `label: true` at or brighter than this magnitude (G for Gaia; V, else G, else J for SIMBAD)
MERGE_ARCSEC = 1.5
EDGE_ARCSEC = 6.0  # one SPHEREx pixel: an object this far outside the frame is still listed (its ring reaches into the picture)
GAIA_MARGIN_ARCSEC = 130.0  # how far the fastest star (10.4″/yr) goes between 2016.0 and the mission
SIMBAD_MARGIN_ARCSEC = 300.0  # the same from 2000.0
V_LIMIT = 20.5
SB_IDENT = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
JPL_WAIT_S = 6.0
JPL_429_TRIES = 6
RETRY_AFTER_S = 60.0  # a catalogue that just failed is not asked again by the route for this long
JPL_TIMEOUT_S = 240.0  # an identification takes JPL up to two minutes
TTL_S = 30 * 86400.0
MAX_EPOCHS = 40
MJD_J2000 = 51544.5


def epoch_mjd(epoch: float) -> float:
    """An epoch as the routes take it: a Julian year (2025.93) or, above 3000, an MJD (61012.4)."""
    return float(epoch) if epoch > 3000.0 else MJD_J2000 + (float(epoch) - 2000.0) * 365.25


def epoch_year(epoch: float) -> float:
    return 2000.0 + (epoch_mjd(epoch) - MJD_J2000) / 365.25


def earth_au(mjd: np.ndarray) -> np.ndarray:
    """The Earth from the barycentre of the Solar System, au, ICRS axes: (E, 3)."""
    _, pvb = erfa.epv00(2400000.5, np.asarray(mjd, "f8") + 69.184 / 86400.0)
    return np.asarray(pvb["p"], "f8").reshape(-1, 3)


def propagate(
    ra: np.ndarray, dec: np.ndarray, pmra: np.ndarray, pmdec: np.ndarray, plx: np.ndarray, cat_epoch: float, epochs: list[float]
) -> tuple[np.ndarray, np.ndarray]:
    """Places (N, E) of catalogue stars at the epochs: the place at `cat_epoch` (degrees, as seen from the barycentre) plus the proper
    motion (mas/yr, pmra with cos dec) plus the parallax (mas) seen from the Earth at each epoch."""
    years = np.array([epoch_year(e) for e in epochs]) - cat_epoch
    earth = earth_au(np.array([epoch_mjd(e) for e in epochs]))
    a, d = np.radians(ra)[:, None], np.radians(dec)[:, None]
    x, y, z = earth[:, 0][None], earth[:, 1][None], earth[:, 2][None]
    p = plx[:, None]
    d_ra = pmra[:, None] * years[None] + p * (x * np.sin(a) - y * np.cos(a))  # mas, on the sky
    d_dec = pmdec[:, None] * years[None] + p * (x * np.cos(a) * np.sin(d) + y * np.sin(a) * np.sin(d) - z * np.cos(d))
    cosd = np.maximum(np.cos(d), 1e-3)
    return (ra[:, None] + d_ra / 3.6e6 / cosd) % 360.0, dec[:, None] + d_dec / 3.6e6


def offsets_arcsec(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """East and north offsets from the frame centre, arcsec (flat sky: frames are at most a degree)."""
    return ((ra - ra0 + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec0)) * 3600.0, (dec - dec0) * 3600.0


def _num(v: Any) -> float | None:
    if v in (None, "", "null"):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _iso(mjd: float) -> str:
    return (datetime(1858, 11, 17, tzinfo=UTC) + timedelta(days=mjd)).strftime("%Y-%m-%d_%H:%M:%S")


def _sexa(deg: float, hours: bool) -> str:
    sign = "-" if deg < 0 else ""
    v = abs(deg) / (15.0 if hours else 1.0)
    a = int(v)
    b = int((v - a) * 60)
    c = ((v - a) * 60 - b) * 60
    return f"{sign}{a:02d}-{b:02d}-{c:05.2f}"


def parse_sexa(text: str, hours: bool) -> float | None:
    """'04 03 45.67' / "+08 23'24.1\"" -> degrees."""
    m = re.findall(r"[-+]?\d+\.?\d*", text.replace("'", " ").replace('"', " ").replace(":", " "))
    if len(m) < 3:
        return None
    a, b, c = abs(float(m[0])), float(m[1]), float(m[2])
    v = a + b / 60.0 + c / 3600.0
    if text.strip().startswith("-"):
        v = -v
    return v * (15.0 if hours else 1.0)


def cache_dir(store_dir: Path) -> Path:
    """Where the kept catalogue answers live: SKY_KNOWN_CACHE, else beside the product database, else in the store."""
    own = os.environ.get("SKY_KNOWN_CACHE")
    if own:
        return Path(own)
    db = os.environ.get("SKY_PRODUCT_DB")
    return (Path(db).resolve().parent if db else store_dir) / "known_field"


class KnownField:
    def __init__(self, store_dir: Path, breakers: dict[str, Breaker] | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.dir = cache_dir(store_dir)
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(45, connect=6), headers={"User-Agent": "spherex-sky/0.1"})
        shared = breakers or {}
        self.breakers = {k: shared.get(k) or Breaker(k) for k in ("simbad", "simbad2", "gaia", "irsa")}
        self.breakers["jpl"] = Breaker("JPL Small-Body Identification")
        self.memory: dict[str, tuple[float, dict[str, Any]]] = {}
        self.tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self.failed: dict[str, tuple[float, str]] = {}

    # ---- kept answers -----------------------------------------------------------------------------------------------------------------
    def _read(self, name: str) -> dict[str, Any] | None:
        hit = self.memory.get(name)
        if hit is not None and time.time() - hit[0] < TTL_S:
            return hit[1]
        path = self.dir / f"{name}.json"
        try:
            if time.time() - path.stat().st_mtime < TTL_S:
                val: dict[str, Any] = json.loads(path.read_text())
                self._remember(name, val)
                return val
        except (OSError, ValueError):
            pass
        return None

    def _remember(self, name: str, val: dict[str, Any]) -> None:
        self.memory[name] = (time.time(), val)
        while len(self.memory) > 300:
            self.memory.pop(next(iter(self.memory)))

    def _write(self, name: str, val: dict[str, Any]) -> None:
        self._remember(name, val)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{name}.part"
            tmp.write_text(json.dumps(val, separators=(",", ":")))
            tmp.replace(self.dir / f"{name}.json")
        except OSError:  # a read-only place: the answer stays in memory
            pass

    def _task(self, name: str, fetch: Any) -> asyncio.Task[dict[str, Any]]:
        """One running fetch per kept answer; it goes on when the request that started it has gone."""
        t = self.tasks.get(name)
        if t is None or (t.done() and (t.cancelled() or t.exception() is not None)):

            async def run() -> dict[str, Any]:
                val: dict[str, Any] = await fetch()
                self._write(name, val)
                return val

            t = self.tasks[name] = asyncio.get_running_loop().create_task(run())
            t.add_done_callback(lambda done: done.cancelled() or done.exception())
            if len(self.tasks) > 500:
                for k in [k for k, v in self.tasks.items() if v.done()][:250]:
                    self.tasks.pop(k, None)
        return t

    async def _kept(self, name: str, fetch: Any, wait_s: float | None = None) -> dict[str, Any]:
        """The kept answer, else the fetch; `{"state": "unavailable"}` when it failed, `{"state": "pending"}` when it has not come in
        `wait_s`."""
        hit = self._read(name)
        if hit is not None:
            return hit
        running = self.tasks.get(name)
        if wait_s is not None and running is not None and not running.done():
            return {"state": "pending"}  # already being asked in the background: only the first ask waits
        failed = self.failed.get(name)
        if wait_s is not None and failed is not None and time.time() - failed[0] < RETRY_AFTER_S:
            return {"state": "unavailable", "error": failed[1]}  # it failed a moment ago: said at once, asked again later
        t = self._task(name, fetch)
        try:
            return await asyncio.wait_for(asyncio.shield(t), wait_s)
        except TimeoutError:
            return {"state": "pending"}
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}"
            self.failed[name] = (time.time(), err)
            while len(self.failed) > 500:
                self.failed.pop(next(iter(self.failed)))
            return {"state": "unavailable", "error": err}

    async def _post(self, which: str, url: str, data: dict[str, str]) -> str:
        br = self.breakers[which]
        br.before()
        try:
            r = await self.client.post(url, data=data)
            r.raise_for_status()
        except Exception:
            br.failure()
            raise
        br.success()
        return r.text

    # ---- Gaia -------------------------------------------------------------------------------------------------------------------------
    async def gaia(self, ra: float, dec: float, size_arcmin: float, wait_s: float | None = None) -> dict[str, Any]:
        radius = (size_arcmin * 60.0 / 2.0 * math.sqrt(2.0) + GAIA_MARGIN_ARCSEC) / 3600.0
        fast2 = GAIA_FAST_KEEP_MAS**2

        def keep(rows: list[dict[str, Any]], via: str) -> dict[str, Any]:
            out: list[list[Any]] = []
            for r in rows:
                a, d = _num(r.get("ra")), _num(r.get("dec"))
                if a is None or d is None:
                    continue
                pa, pd, g = _num(r.get("pmra")) or 0.0, _num(r.get("pmdec")) or 0.0, _num(r.get("phot_g_mean_mag"))
                if (g is None or g > GAIA_MAG_LIMIT) and pa * pa + pd * pd < fast2:
                    continue
                plx = _num(r.get("parallax")) or 0.0
                # BP−RP and RUWE ride in the same request: the passport shows them for a ringed star without asking Gaia again
                out.append([str(r.get("source_id")), a, d, pa, pd, max(plx, 0.0), g, _num(r.get("bp_rp")), _num(r.get("ruwe"))])
            out.sort(key=lambda s: 99.0 if s[6] is None else s[6])
            return {"state": "ok", "via": via, "truncated": len(out) > GAIA_MAX_ROWS, "rows": out[:GAIA_MAX_ROWS]}

        async def fetch() -> dict[str, Any]:
            err: Exception | None = None
            for url, table, label in GAIA_TAPS:
                adql = (
                    f"SELECT TOP {GAIA_MAX_ROWS + 1} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, bp_rp, ruwe FROM {table} "
                    f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius:.6f})) "
                    f"AND (phot_g_mean_mag <= {GAIA_MAG_LIMIT} OR pmra*pmra + pmdec*pmdec >= {fast2:.0f}) ORDER BY phot_g_mean_mag"
                )
                try:
                    body = await self._post("gaia", url, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql})
                    if body.lstrip().startswith("<"):
                        raise ValueError("the archive answered an error page")
                    return keep(list(csv.DictReader(io.StringIO(body))), label)
                except (httpx.HTTPError, BreakerOpenError, ValueError) as e:
                    err = e
            try:  # the same catalogue from the Parquet copy on AWS
                rows = await cpu.run(gaia_cloud_rows, ra, dec, radius * 3600.0)
            except Exception as e:
                raise (err or e) from e
            return keep(rows, "Gaia DR3 on AWS (the archives did not answer)")

        return await self._kept(f"gaia_{ra:.4f}_{dec:.4f}_{size_arcmin:.1f}", fetch, wait_s)

    # ---- SIMBAD -----------------------------------------------------------------------------------------------------------------------
    async def simbad(self, ra: float, dec: float, size_arcmin: float, wait_s: float | None = None) -> dict[str, Any]:
        radius = (size_arcmin * 60.0 / 2.0 * math.sqrt(2.0) + SIMBAD_MARGIN_ARCSEC) / 3600.0
        adql = (
            f"SELECT TOP {SIMBAD_MAX_ROWS + 1} b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.plx_value, b.otype, f.V, f.G, f.J "
            "FROM basic AS b LEFT JOIN allfluxes AS f ON f.oidref = b.oid "
            f"WHERE CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {radius:.5f})) = 1 "
            "AND (b.otype IS NULL OR b.otype NOT IN ('Pl', 'Pl?', 'PlG', 'PlG?'))"
        )

        async def fetch() -> dict[str, Any]:
            err: Exception | None = None
            for which, base in (("simbad", SIMBAD_BASES[0]), ("simbad2", SIMBAD_BASES[1])):
                try:
                    body = await self._post(which, base, {"request": "doQuery", "lang": "adql", "format": "json", "query": adql})
                    data = json.loads(body).get("data")
                    if data is None:
                        raise ValueError("SIMBAD answered without data")
                    rows = []
                    for r in data:
                        if r[1] is None or r[2] is None:
                            continue
                        pm_plx = [_num(r[3]) or 0.0, _num(r[4]) or 0.0, max(_num(r[5]) or 0.0, 0.0)]
                        rows.append([str(r[0]), float(r[1]), float(r[2]), *pm_plx, r[6], _num(r[7]), _num(r[8]), _num(r[9])])
                    return {"state": "ok", "via": "SIMBAD", "truncated": len(rows) > SIMBAD_MAX_ROWS, "rows": rows[:SIMBAD_MAX_ROWS]}
                except (httpx.HTTPError, BreakerOpenError, ValueError) as e:
                    err = e
            raise err or RuntimeError("no SIMBAD mirror")

        return await self._kept(f"simbad_{ra:.4f}_{dec:.4f}_{size_arcmin:.1f}", fetch, wait_s)

    # ---- JPL --------------------------------------------------------------------------------------------------------------------------
    async def jpl_at(self, ra: float, dec: float, size_arcmin: float, mjd: float, wait_s: float | None = JPL_WAIT_S) -> dict[str, Any]:
        """The Solar System bodies JPL puts in the frame at `mjd` (geocentric): rows [name, ra, dec, V, ra rate, dec rate (″/h)]."""
        half = size_arcmin / 60.0 / 2.0 + EDGE_ARCSEC / 3600.0
        params = {
            "mpc-code": "500",
            "obs-time": _iso(mjd),
            "fov-ra-center": _sexa(ra, True),
            "fov-dec-center": _sexa(dec, False),
            "fov-ra-hwidth": f"{half / max(0.05, math.cos(math.radians(dec))):.4f}",
            "fov-dec-hwidth": f"{half:.4f}",
            "vmag-lim": str(V_LIMIT),
            "two-pass": "true",
            "suppress-first-pass": "true",
        }

        async def fetch() -> dict[str, Any]:
            br = self.breakers["jpl"]
            br.before()
            try:
                for _ in range(JPL_429_TRIES):
                    # a 429 (JPL's limit of 20 identifications in five minutes) is not a failure: the lane waits the time JPL asks
                    # (service.upstream.jpl) and the same request is made again
                    r = await jpl.turn(lambda: self.client.get(SB_IDENT, params=params, timeout=JPL_TIMEOUT_S), "sbident")
                    if r.status_code != 429:
                        break
                r.raise_for_status()
                body = r.json()
                # a frame without bodies is an answer too: JPL says "no matching records" and gives no table
                empty = "signature" in body and "no matching records" in str(body.get("warning", ""))
                if "fields_second" not in body and "n_second_pass" not in body and not empty:
                    raise ValueError(str(body)[:160])
            except Exception:
                br.failure()
                raise
            br.success()
            fields = body.get("fields_second") or []
            col = {name: i for i, name in enumerate(fields)}
            rows = []
            for row in body.get("data_second_pass") or []:
                try:
                    a = parse_sexa(str(row[col["Astrometric RA (hh:mm:ss)"]]), True)
                    d = parse_sexa(str(row[col["Astrometric Dec (dd mm'ss\")"]]), False)
                    if a is None or d is None:
                        continue
                    rows.append(
                        [
                            str(row[col["Object name"]]).strip(),
                            round(a, 6),
                            round(d, 6),
                            _num(row[col["Visual magnitude (V)"]]),
                            _num(row[col['RA rate ("/h)']]),
                            _num(row[col['Dec rate ("/h)']]),
                        ]
                    )
                except (KeyError, IndexError):
                    continue
            return {"state": "ok", "via": "JPL Small-Body Identification, geocentric", "rows": rows}

        name = "jpl_" + digest(f"{ra:.4f}|{dec:.4f}|{size_arcmin:.1f}|{mjd:.3f}")[:24]
        return await self._kept(name, fetch, wait_s)

    # ---- the frame --------------------------------------------------------------------------------------------------------------------
    # ---- the deeper catalogues, for matching -----------------------------------------------------------------------------------------
    async def gaia_deep(self, ra: float, dec: float, size_arcmin: float, wait_s: float | None = MATCH_WAIT_S) -> dict[str, Any]:
        """Gaia stars of GAIA_MAG_LIMIT < G <= GAIA_MATCH_MAG in the frame (rows as in `gaia`)."""
        radius = (size_arcmin * 60.0 / 2.0 * math.sqrt(2.0) + GAIA_MARGIN_ARCSEC) / 3600.0

        async def fetch() -> dict[str, Any]:
            err: Exception | None = None
            for url, table, label in GAIA_TAPS:
                adql = (
                    f"SELECT TOP {MATCH_MAX_ROWS + 1} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag FROM {table} "
                    f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius:.6f})) "
                    f"AND phot_g_mean_mag > {GAIA_MAG_LIMIT} AND phot_g_mean_mag <= {GAIA_MATCH_MAG}"
                )
                try:
                    body = await self._post("gaia", url, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql})
                    if body.lstrip().startswith("<"):
                        raise ValueError("the archive answered an error page")
                    rows = []
                    for r in csv.DictReader(io.StringIO(body)):
                        a, d = _num(r.get("ra")), _num(r.get("dec"))
                        if a is None or d is None:
                            continue
                        rows.append(
                            [
                                str(r.get("source_id")),
                                a,
                                d,
                                _num(r.get("pmra")) or 0.0,
                                _num(r.get("pmdec")) or 0.0,
                                0.0,
                                _num(r.get("phot_g_mean_mag")),
                            ]
                        )
                    return {"state": "ok", "via": label, "truncated": len(rows) > MATCH_MAX_ROWS, "rows": rows[:MATCH_MAX_ROWS]}
                except (httpx.HTTPError, BreakerOpenError, ValueError) as e:
                    err = e
            raise err or RuntimeError("no Gaia archive answered")

        return await self._kept(f"gaiadeep_{ra:.4f}_{dec:.4f}_{size_arcmin:.1f}", fetch, wait_s)

    async def irsa_match(self, cat: str, ra: float, dec: float, size_arcmin: float, wait_s: float | None = MATCH_WAIT_S) -> dict[str, Any]:
        """One IRSA catalogue over the frame: rows [id, ra, dec, pmra_mas, pmdec_mas, mag] at the catalogue's epoch."""
        table, cols, _, _, _, pm_unit = IRSA_MATCH[cat]
        margin = SIMBAD_MARGIN_ARCSEC if pm_unit else EDGE_ARCSEC + MATCH_MERGE_ARCSEC
        radius = (size_arcmin * 60.0 / 2.0 * math.sqrt(2.0) + margin) / 3600.0

        async def fetch() -> dict[str, Any]:
            adql = (
                f"SELECT TOP {MATCH_MAX_ROWS + 1} {cols} FROM {table} "
                f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius:.6f})) = 1"
            )
            br = self.breakers["irsa"]
            br.before()
            try:
                r = await self.client.post(IRSA_TAP, data={"LANG": "ADQL", "FORMAT": "csv", "QUERY": adql}, timeout=90.0)
                r.raise_for_status()
                if r.text.lstrip().startswith("<"):
                    raise ValueError("IRSA answered an error page")
            except Exception:
                br.failure()
                raise
            br.success()
            rows = []
            for row in csv.DictReader(io.StringIO(r.text)):
                a, d = _num(row.get("ra")), _num(row.get("dec"))
                if a is None or d is None:
                    continue
                k = pm_unit or 0.0
                rows.append(
                    [
                        str(row.get("id") or "").strip(),
                        a,
                        d,
                        (_num(row.get("pmra")) or 0.0) * k,
                        (_num(row.get("pmdec")) or 0.0) * k,
                        _num(row.get("mag")),
                    ]
                )
            return {"state": "ok", "via": f"IRSA {table}", "truncated": len(rows) > MATCH_MAX_ROWS, "rows": rows[:MATCH_MAX_ROWS]}

        return await self._kept(f"{cat}_{ra:.4f}_{dec:.4f}_{size_arcmin:.1f}", fetch, wait_s)

    def _deep_parts(self, ra: float, dec: float, size_arcmin: float, wait_s: float | None) -> list[Any]:
        return [self.gaia_deep(ra, dec, size_arcmin, wait_s), *(self.irsa_match(c, ra, dec, size_arcmin, wait_s) for c in IRSA_MATCH)]

    async def objects(
        self,
        ra: float,
        dec: float,
        size_arcmin: float,
        epochs: list[float],
        jpl_wait_s: float | None = JPL_WAIT_S,
        match: bool = True,
        match_wait_s: float | None = MATCH_WAIT_S,
        wait_s: float | None = None,
    ) -> dict[str, Any]:
        """`wait_s`: answer within about this long with whatever is ready; a catalogue still being asked is `pending` and goes on in the
        background (the next ask has it). None waits for Gaia and SIMBAD (the checks before submit need them)."""
        epochs = epochs[:MAX_EPOCHS]
        if wait_s is not None:
            jpl_wait_s = wait_s if jpl_wait_s is None else min(jpl_wait_s, wait_s)
            match_wait_s = wait_s if match_wait_s is None else min(match_wait_s, wait_s)
        deep: dict[str, Any] = {}
        gaia, simbad, *rest = await asyncio.gather(
            self.gaia(ra, dec, size_arcmin, wait_s),
            self.simbad(ra, dec, size_arcmin, wait_s),
            *(self.jpl_at(ra, dec, size_arcmin, epoch_mjd(e), jpl_wait_s) for e in epochs),
            *(self._deep_parts(ra, dec, size_arcmin, match_wait_s) if match else []),
        )
        bodies = rest[: len(epochs)]
        if match:
            deep = {"gaia": rest[len(epochs)], **dict(zip(IRSA_MATCH, rest[len(epochs) + 1 :], strict=True))}
        return await asyncio.to_thread(assemble, ra, dec, size_arcmin, epochs, gaia, simbad, bodies, deep)


def _status(part: dict[str, Any]) -> dict[str, Any]:
    out = {k: part[k] for k in ("state", "via", "error", "truncated") if part.get(k) not in (None, False)}
    if part.get("state") == "ok":
        out["count"] = len(part["rows"])
    return out


def assemble(
    ra: float,
    dec: float,
    size_arcmin: float,
    epochs: list[float],
    gaia: dict[str, Any],
    simbad: dict[str, Any],
    bodies: list[dict[str, Any]],
    deep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The answer of `GET /api/known` from the kept catalogue rows: places at every epoch, the frame cut, SIMBAD names on Gaia stars;
    with `deep` (the deeper catalogues) also the `match` list."""
    half = size_arcmin * 60.0 / 2.0 + EDGE_ARCSEC
    objects: list[dict[str, Any]] = []

    def inside(pr: np.ndarray, pd: np.ndarray) -> np.ndarray:
        east, north = offsets_arcsec(pr, pd, ra, dec)
        return np.asarray(((np.abs(east) <= half) & (np.abs(north) <= half)).any(axis=1))

    def places(pr: np.ndarray, pd: np.ndarray, i: int) -> list[dict[str, float]]:
        return [{"epoch": e, "ra": round(float(pr[i, k]), 6), "dec": round(float(pd[i, k]), 6)} for k, e in enumerate(epochs)]

    names: dict[int, tuple[str, str | None]] = {}  # Gaia row -> the SIMBAD name and type of the same star
    g_rows = gaia.get("rows") or []
    s_rows = simbad.get("rows") or []
    g = np.array([r[1:6] for r in g_rows], float).reshape(-1, 5)
    s = np.array([r[1:6] for r in s_rows], float).reshape(-1, 5)
    s_merged = np.zeros(len(s_rows), bool)
    if len(g) and len(s):
        # SIMBAD's stars at Gaia's epoch against Gaia's places, on the frame's flat sky
        sr, sd = propagate(s[:, 0], s[:, 1], s[:, 2], s[:, 3], np.zeros(len(s)), SIMBAD_EPOCH, [GAIA_EPOCH])
        tree = cKDTree(np.column_stack(offsets_arcsec(g[:, 0], g[:, 1], ra, dec)))
        dist, idx = tree.query(np.column_stack(offsets_arcsec(sr[:, 0], sd[:, 0], ra, dec)), distance_upper_bound=MERGE_ARCSEC)
        for j in np.argsort(dist):
            if not np.isfinite(dist[j]):
                break
            if int(idx[j]) not in names:
                names[int(idx[j])] = (display_name(str(s_rows[j][0]), []), s_rows[j][6])
                s_merged[j] = True
    if len(g) and epochs:
        pr, pd = propagate(g[:, 0], g[:, 1], g[:, 2], g[:, 3], g[:, 4], GAIA_EPOCH, epochs)
        for i in np.flatnonzero(inside(pr, pd)):
            r = g_rows[i]
            pm = math.hypot(r[3], r[4])
            name, otype = names.get(int(i), (None, None))
            objects.append(
                {
                    "id": f"gaia:{r[0]}",
                    "source": "gaia",
                    "name": name or f"Gaia DR3 {r[0]}",
                    "type": otype or "star",
                    "mag": None if r[6] is None else round(r[6], 2),
                    "mag_band": "G",
                    "pm_total": round(pm, 1),
                    "pmra": round(r[3], 1),
                    "pmdec": round(r[4], 1),
                    "parallax": round(r[5], 2),
                    **({"bp_rp": round(r[7], 3)} if len(r) > 7 and r[7] is not None else {}),
                    **({"ruwe": round(r[8], 2)} if len(r) > 8 and r[8] is not None else {}),
                    "positions": places(pr, pd, int(i)),
                    "label": bool(pm >= FAST_MAS or (r[6] is not None and r[6] <= BRIGHT_MAG)),
                    **({"simbad": name} if name else {}),
                }
            )
    if len(s) and epochs:
        pr, pd = propagate(s[:, 0], s[:, 1], s[:, 2], s[:, 3], s[:, 4], SIMBAD_EPOCH, epochs)
        for i in np.flatnonzero(inside(pr, pd) & ~s_merged):
            r = s_rows[i]
            pm = math.hypot(r[3], r[4])
            mag, band = next(((m, b) for m, b in zip(r[7:10], "VGJ", strict=True) if m is not None), (None, None))
            objects.append(
                {
                    "id": f"simbad:{r[0]}",
                    "source": "simbad",
                    "name": display_name(str(r[0]), []),
                    "type": r[6],
                    "mag": None if mag is None else round(mag, 2),
                    "mag_band": band,
                    "pm_total": round(pm, 1),
                    "pmra": round(r[3], 1),
                    "pmdec": round(r[4], 1),
                    "parallax": round(r[5], 2),
                    "positions": places(pr, pd, int(i)),
                    "label": bool(pm >= FAST_MAS or (mag is not None and mag <= BRIGHT_MAG)),
                }
            )
    seen: dict[str, dict[str, Any]] = {}
    for e, part in zip(epochs, bodies, strict=True):
        for name, a, d, v, vra, vdec in part.get("rows") or []:
            rate = None if vra is None or vdec is None else math.hypot(vra, vdec)
            o = seen.get(name)
            if o is None:
                o = seen[name] = {
                    "id": f"jpl:{name}",
                    "source": "jpl",
                    "name": name,
                    "type": "comet" if re.match(r"^(\d+[PDI]\b|[CPDI]/)", name) else "asteroid",
                    "mag": v,
                    "mag_band": "V",
                    "pm_total": None if rate is None else round(rate * 8766.0 * 1000.0),  # mas/yr, as for the stars
                    "rate_arcsec_h": None if rate is None else round(rate, 2),
                    "positions": [],
                    "label": True,
                }
                objects.append(o)
            o["positions"].append({"epoch": e, "ra": a, "dec": d})
    jpl_states = [str(b.get("state")) for b in bodies]
    jpl_status: dict[str, Any] = {
        "state": "ok" if all(x == "ok" for x in jpl_states) else ("unavailable" if "unavailable" in jpl_states else "pending"),
        "via": "JPL Small-Body Identification, geocentric",
        "epochs_answered": jpl_states.count("ok"),
        "epochs": len(epochs),
        "count": len(seen),
    }
    err = next((b.get("error") for b in bodies if b.get("error")), None)
    if err:
        jpl_status["error"] = err
    out_match, match_status = match_list(ra, dec, size_arcmin, epochs, objects, deep) if deep else ([], {})
    return {
        "objects": objects,
        "match": out_match,
        "answered": {
            "gaia": gaia.get("state") == "ok",
            "simbad": simbad.get("state") == "ok",
            "jpl": bool(epochs) and jpl_status["state"] == "ok",
        },
        "status": {"gaia": _status(gaia), "simbad": _status(simbad), "jpl": jpl_status, **({"match": match_status} if deep else {})},
        "frame": {"ra": ra, "dec": dec, "size_arcmin": size_arcmin, "epochs": epochs},
        "thresholds": {
            "label_pm_mas_yr": FAST_MAS,
            "label_mag": BRIGHT_MAG,
            "gaia_mag_limit": GAIA_MAG_LIMIT,
            "gaia_fast_keep_mas_yr": GAIA_FAST_KEEP_MAS,
            "jpl_v_limit": V_LIMIT,
            "gaia_match_mag": GAIA_MATCH_MAG,
            "match_merge_arcsec": MATCH_MERGE_ARCSEC,
        },
    }


def match_list(
    ra: float, dec: float, size_arcmin: float, epochs: list[float], objects: list[dict[str, Any]], deep: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The deeper catalogues merged by position, without the sources already in `objects`: [{id, catalogues, ids, mag, mag_band,
    pm_total, positions}] and the state of each catalogue. Sources are taken in the order CatWISE2020 (it has its own motion), Gaia,
    AllWISE, 2MASS: the first one's place and motion stand for the merged source."""
    status = {c: _status(deep.get(c) or {"state": "unavailable"}) for c in ("gaia", "catwise", "allwise", "twomass")}
    if not epochs:
        return [], status
    half = size_arcmin * 60.0 / 2.0 + EDGE_ARCSEC
    src: list[tuple[str, str, float | None, str, float]] = []  # (catalogue, id, mag, band, pm_total)
    pos_r: list[np.ndarray] = []
    pos_d: list[np.ndarray] = []
    for cat in ("catwise", "gaia", "allwise", "twomass"):
        part = deep.get(cat) or {}
        rows = (part.get("rows") or []) if part.get("state") == "ok" else []
        if not rows:
            continue
        if cat == "gaia":
            a = np.array([r[1:6] for r in rows], float).reshape(-1, 5)
            pr, pd = propagate(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], GAIA_EPOCH, epochs)
            mags, band = [r[6] for r in rows], "G"
        else:
            _, _, epoch, _, band, _ = IRSA_MATCH[cat]
            a = np.array([r[1:5] for r in rows], float).reshape(-1, 4)
            pr, pd = propagate(a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.zeros(len(a)), epoch, epochs)
            mags = [r[5] for r in rows]
        for i, r in enumerate(rows):
            src.append((cat, str(r[0]), mags[i], band, float(math.hypot(a[i, 2], a[i, 3]))))
        pos_r.append(pr)
        pos_d.append(pd)
    if not src:
        return [], status
    pr = np.vstack(pos_r)
    pd = np.vstack(pos_d)
    east, north = offsets_arcsec(pr, pd, ra, dec)
    keep = ((np.abs(east) <= half) & (np.abs(north) <= half)).any(axis=1)
    # the sources already drawn (objects) at the first epoch: a deep source on one of them is that object
    drawn = [(o["positions"][0]["ra"], o["positions"][0]["dec"]) for o in objects if o.get("positions")]
    xy = np.column_stack(offsets_arcsec(pr[:, 0], pd[:, 0], ra, dec))
    if drawn:
        dr = np.array(drawn, float)
        d_tree = cKDTree(np.column_stack(offsets_arcsec(dr[:, 0], dr[:, 1], ra, dec)))
        dist, _ = d_tree.query(xy, distance_upper_bound=MATCH_MERGE_ARCSEC)
        keep &= ~np.isfinite(dist)
    idx = np.flatnonzero(keep)
    out: list[dict[str, Any]] = []
    if not len(idx):
        return out, status
    tree = cKDTree(xy[idx])
    taken = np.zeros(len(idx), bool)
    for k in range(len(idx)):  # in catalogue order: the first source of a group stands for it
        if taken[k]:
            continue
        group = [j for j in tree.query_ball_point(xy[idx[k]], MATCH_MERGE_ARCSEC) if not taken[j]]
        for j in group:
            taken[j] = True
        i = int(idx[k])
        cat, sid, mag, band, pm = src[i]
        members = [src[int(idx[j])] for j in sorted(group)]
        out.append(
            {
                "id": f"{cat}:{sid}",
                "catalogues": sorted({MATCH_NAMES[m[0]] for m in members}),
                "ids": [f"{m[0]}:{m[1]}" for m in members],
                "mag": None if mag is None else round(float(mag), 2),
                "mag_band": band,
                "pm_total": round(pm, 1),
                "positions": [
                    {"epoch": e, "ra": round(float(pr[i, n]), 6), "dec": round(float(pd[i, n]), 6)} for n, e in enumerate(epochs)
                ],
            }
        )
    for c in status:
        status[c]["merged_into"] = sum(1 for o in out if MATCH_NAMES[c] in o["catalogues"])
    return out, status
