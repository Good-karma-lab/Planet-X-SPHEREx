"""What the catalogues know about a place at an epoch: SIMBAD, Gaia DR3, 2MASS, AllWISE, CatWISE, VSX and NED, asked together.

Every catalogue has its own state: `ok` (the nearest object inside the radius, at the epoch), `none` (asked, nothing there) or
`unavailable` (it did not answer: never shown as "nothing"). Only `ok` and `none` are kept (one week). The surveys of other epochs are
searched where the star was then, so Gaia (or SIMBAD) is asked first for the proper motion.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from service.upstream.breaker import Breaker, BreakerOpenError

SIMBAD_BASES = ("https://simbad.harvard.edu/simbad/sim-tap/sync", "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
GAIA_TAPS = (
    ("https://gea.esac.esa.int/tap-server/tap/sync", "gaiadr3.gaia_source", "Gaia DR3 archive"),
    ("https://dc.g-vo.org/tap/sync", "gaia.dr3lite", "GAVO dr3lite: no variability flag"),
)
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
VIZIER_TAP = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
NED_URL = "https://ned.ipac.caltech.edu/cgi-bin/objsearch"
SIMBAD_EPOCH, GAIA_EPOCH = 2000.0, 2016.0
IDENT_FAST_MAS = 1000.0  # above this proper motion (mas/yr) a star can have left the small cone: SIMBAD is asked for those in a wide cone
IDENT_MAX_MAS = 11000.0  # the fastest star known (Barnard's Star) moves 10.4 arcsec/yr
DEADLINE_S = 12.0
TTL_S = 7 * 86400.0

# IRSA tables: part -> (table, columns, survey epoch, {output key: column})
IRSA_PARTS: dict[str, tuple[str, str, float, dict[str, str]]] = {
    "twomass": (
        "fp_psc",
        "designation, ra, dec, j_m, h_m, k_m, ph_qual",
        1999.5,
        {"designation": "designation", "J": "j_m", "H": "h_m", "Ks": "k_m", "qual": "ph_qual"},
    ),
    "allwise": (
        "allwise_p3as_psd",
        "designation, ra, dec, w1mpro, w2mpro, w3mpro, w4mpro, cc_flags",
        2010.5,
        {"designation": "designation", "W1": "w1mpro", "W2": "w2mpro", "W3": "w3mpro", "W4": "w4mpro", "cc_flags": "cc_flags"},
    ),
    "catwise": (
        "catwise_2020",
        "source_name, ra, dec, w1mpro, w2mpro, pmra, pmdec",
        2015.4,
        {"name": "source_name", "W1": "w1mpro", "W2": "w2mpro", "pmra": "pmra", "pmdec": "pmdec"},
    ),
}
TEXT_KEYS = ("designation", "name", "cc_flags", "qual")


def sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    return math.hypot(((ra1 - ra2 + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec2)), dec1 - dec2) * 3600.0


def moved(ra: float, dec: float, pmra: float | None, pmdec: float | None, dt_years: float) -> tuple[float, float]:
    """The place after dt_years with a proper motion in mas/yr (pmra includes cos dec)."""
    d = dec + (pmdec or 0.0) * dt_years / 3.6e6
    return ra + (pmra or 0.0) * dt_years / 3.6e6 / max(math.cos(math.radians(dec)), 1e-3), d


def mag_shown(v: float | None, g: float | None, j: float | None) -> dict[str, Any] | None:
    """The magnitude the page shows: V when SIMBAD has it, else Gaia G, else 2MASS J."""
    for val, band in ((v, "V"), (g, "G"), (j, "J")):
        if val is not None:
            return {"value": round(float(val), 2), "band": band}
    return None


def _fnum(row: dict[str, str], key: str) -> float | None:
    v = row.get(key)
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_simbad(rows: list[list[Any]], ra: float, dec: float, epoch: float, radius: float) -> dict[str, Any]:
    """The nearest SIMBAD object at `epoch` (its proper motion applied), preferring one with a magnitude."""
    best: tuple[float, dict[str, Any]] | None = None
    for r in rows:
        a, d = moved(r[1], r[2], r[3], r[4], epoch - SIMBAD_EPOCH)
        sep = sep_arcsec(a, d, ra, dec)
        mag = mag_shown(r[9], r[10], r[11])
        score = sep + (0.0 if mag else 2.0)
        if sep <= radius and (best is None or score < best[0]):
            best = (
                score,
                {
                    "state": "ok",
                    "name": r[0],
                    "otype": r[6],
                    "sptype": r[7] or None,
                    "bibcount": r[8],
                    "link": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=" + quote(r[0]),
                    "sep": round(sep, 2),
                    "pmra": r[3],
                    "pmdec": r[4],
                    "plx": r[5],
                    "mag": mag,
                },
            )
    return best[1] if best else {"state": "none"}


def parse_gaia(body: str, ra: float, dec: float, epoch: float, radius: float, label: str) -> dict[str, Any]:
    best: tuple[float, dict[str, Any]] | None = None
    for row in csv.DictReader(io.StringIO(body)):
        a0, d0 = _fnum(row, "ra"), _fnum(row, "dec")
        if a0 is None or d0 is None:
            continue
        pa, pd = _fnum(row, "pmra") or 0.0, _fnum(row, "pmdec") or 0.0
        a, d = moved(a0, d0, pa, pd, epoch - GAIA_EPOCH)
        sep = sep_arcsec(a, d, ra, dec)
        if sep <= radius and (best is None or sep < best[0]):
            bp, rp = _fnum(row, "phot_bp_mean_mag"), _fnum(row, "phot_rp_mean_mag")
            bprp = _fnum(row, "bp_rp") if "bp_rp" in row else (bp - rp if bp is not None and rp is not None else None)
            best = (
                sep,
                {
                    "state": "ok",
                    "source_id": row["source_id"],
                    "g": _fnum(row, "phot_g_mean_mag"),
                    "bp_rp": None if bprp is None else round(bprp, 3),
                    "parallax": _fnum(row, "parallax"),
                    "parallax_err": _fnum(row, "parallax_error"),
                    "ruwe": _fnum(row, "ruwe"),
                    "pmra": pa,
                    "pmdec": pd,
                    "variable": row.get("phot_variable_flag") if "phot_variable_flag" in row else None,
                    "sep": round(sep, 2),
                    "source": label,
                },
            )
    return best[1] if best else {"state": "none", "source": label}


def parse_irsa(body: str, ra: float, dec: float, radius: float, labels: dict[str, str]) -> dict[str, Any]:
    best: tuple[float, dict[str, Any]] | None = None
    for row in csv.DictReader(io.StringIO(body)):
        a, d = _fnum(row, "ra"), _fnum(row, "dec")
        if a is None or d is None:
            continue
        sep = sep_arcsec(a, d, ra, dec)
        if sep <= radius + 1.0 and (best is None or sep < best[0]):
            vals = {k: (row.get(c) if k in TEXT_KEYS else _fnum(row, c)) for k, c in labels.items()}
            best = (sep, {"state": "ok", "sep": round(sep, 2), **vals})
    return best[1] if best else {"state": "none"}


def parse_vsx(body: str, ra: float, dec: float) -> dict[str, Any]:
    best: tuple[float, dict[str, Any]] | None = None
    for row in csv.DictReader(io.StringIO(body)):
        a, d = _fnum(row, "RAJ2000"), _fnum(row, "DEJ2000")
        if a is None or d is None:
            continue
        sep = sep_arcsec(a, d, ra, dec)
        if best is None or sep < best[0]:
            best = (
                sep,
                {
                    "state": "ok",
                    "name": row.get("Name"),
                    "type": row.get("Type") or None,
                    "period": row.get("Period") or None,
                    "sep": round(sep, 2),
                },
            )
    return best[1] if best else {"state": "none"}


def parse_ned(body: str) -> dict[str, Any]:
    rows = [ln.split("|") for ln in body.splitlines() if ln.count("|") >= 8 and not ln.lower().startswith("no.")]
    best: tuple[float, dict[str, Any]] | None = None
    for c in rows:
        try:
            sep = float(c[8]) * 60.0
            name, typ, z = c[1].strip(), c[4].strip(), c[6].strip()
        except (ValueError, IndexError):
            continue
        if best is None or sep < best[0]:
            zv = float(z) if z.replace(".", "", 1).replace("-", "", 1).isdigit() else None
            best = (sep, {"state": "ok", "name": name, "type": typ or None, "z": zv, "sep": round(sep, 2)})
    if best is None and "<html" in body[:200].lower():
        raise RuntimeError("NED answered a page, not a table")
    return best[1] if best else {"state": "none"}


class CatalogueContext:
    """The seven catalogues for a place, asked together, each behind its own breaker, cached for a week (answers 'ok' and 'none' only)."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(DEADLINE_S, connect=4.0), headers={"User-Agent": "spherex-sky/0.1"})
        self.breakers = {k: Breaker(k) for k in ("simbad", "gaia", "irsa", "vizier", "ned")}
        self.cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    async def _get(self, which: str, url: str, params: dict[str, str] | None = None, form: bool = False) -> str:
        br = self.breakers[which]
        br.before()
        try:
            r = await (self.client.post(url, data=params) if form else self.client.get(url, params=params))
            r.raise_for_status()
        except Exception:
            br.failure()
            raise
        br.success()
        return r.text

    async def _tap(self, which: str, url: str, adql: str, fmt: str = "csv") -> str:
        return await self._get(which, url, {"LANG": "ADQL", "FORMAT": fmt, "QUERY": adql}, form=True)

    async def _simbad(self, ra: float, dec: float, epoch: float, radius: float) -> dict[str, Any]:
        yrs = abs(epoch - SIMBAD_EPOCH)
        small, big = (radius + yrs + 1.0) / 3600.0, (radius + IDENT_MAX_MAS / 1000.0 * yrs + 1.0) / 3600.0
        adql = (
            "SELECT TOP 400 b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.plx_value, b.otype, b.sp_type, b.nbref, f.V, f.G, f.J "
            "FROM basic AS b LEFT JOIN allfluxes AS f ON f.oidref = b.oid "
            f"WHERE (CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {small:.5f})) = 1 "
            f"OR (CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {big:.5f})) = 1 "
            f"AND (b.pmra*b.pmra + b.pmdec*b.pmdec) > {IDENT_FAST_MAS**2:.0f})) "
            "AND (b.otype IS NULL OR b.otype NOT IN ('Pl', 'Pl?', 'PlG', 'PlG?'))"
        )
        err: Exception | None = None
        for base in SIMBAD_BASES:
            try:
                body = await self._get("simbad", base, {"request": "doQuery", "lang": "adql", "format": "json", "query": adql})
                return parse_simbad(json.loads(body).get("data") or [], ra, dec, epoch, radius)
            except (httpx.HTTPError, BreakerOpenError, ValueError) as e:
                err = e
        raise err or RuntimeError("no SIMBAD mirror")

    async def _gaia(self, ra: float, dec: float, epoch: float, radius: float) -> dict[str, Any]:
        cone = (radius + IDENT_MAX_MAS / 1000.0 * abs(epoch - GAIA_EPOCH) + 1.0) / 3600.0
        cols = {
            "gaiadr3.gaia_source": (
                "source_id, ra, dec, pmra, pmdec, parallax, parallax_error, phot_g_mean_mag, bp_rp, ruwe, phot_variable_flag"
            ),
            "gaia.dr3lite": (
                "source_id, ra, dec, pmra, pmdec, parallax, parallax_error, phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag, ruwe"
            ),
        }
        err: Exception | None = None
        for url, table, label in GAIA_TAPS:
            adql = (
                f"SELECT TOP 300 {cols[table]} FROM {table} "
                f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {cone:.6f}))"
            )
            try:
                return parse_gaia(await self._tap("gaia", url, adql), ra, dec, epoch, radius, label)
            except (httpx.HTTPError, BreakerOpenError) as e:
                err = e
        raise err or RuntimeError("no Gaia archive")

    async def _irsa(self, part: str, ra: float, dec: float, radius: float) -> dict[str, Any]:
        table, cols, _, labels = IRSA_PARTS[part]
        cone = (radius + 1.0) / 3600.0
        adql = (
            f"SELECT TOP 10 {cols} FROM {table} WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {cone:.6f})) = 1"
        )
        return parse_irsa(await self._tap("irsa", IRSA_TAP, adql), ra, dec, radius, labels)

    async def _vsx(self, ra: float, dec: float, radius: float) -> dict[str, Any]:
        adql = (
            'SELECT TOP 5 "Name", "Type", "Period", "RAJ2000", "DEJ2000" FROM "B/vsx/vsx" '
            f"WHERE 1=CONTAINS(POINT('ICRS', \"RAJ2000\", \"DEJ2000\"), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius / 3600.0:.6f}))"
        )
        return parse_vsx(await self._tap("vizier", VIZIER_TAP, adql), ra, dec)

    async def _ned(self, ra: float, dec: float, radius: float) -> dict[str, Any]:
        params = {
            "search_type": "Near Position Search",
            "in_csys": "Equatorial",
            "in_equinox": "J2000.0",
            "lon": f"{ra:.6f}d",
            "lat": f"{dec:.6f}d",
            "radius": f"{radius / 60.0:.4f}",
            "out_csys": "Equatorial",
            "out_equinox": "J2000.0",
            "of": "ascii_bar",
            "zv_breaker": "30000.0",
            "list_limit": "5",
            "img_stamp": "NO",
        }
        return parse_ned(await self._get("ned", NED_URL, params))

    async def _kept(self, key: str, fetch: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        hit = self.cache.get(key)
        if hit is not None and time.time() - hit[0] < TTL_S:
            return hit[1]
        try:
            out = await asyncio.wait_for(fetch(), timeout=DEADLINE_S * 2 + 5)
        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError) and not isinstance(e.__cause__, TimeoutError):
                raise
            return {"state": "unavailable", "error": str(e)[:120]}
        if out.get("state") in ("ok", "none"):
            self.cache[key] = (time.time(), out)
            while len(self.cache) > 4000:
                self.cache.popitem(last=False)
        return out

    async def context(self, ra: float, dec: float, epoch: float = 2025.9, radius: float = 6.0) -> dict[str, Any]:
        t0 = time.time()
        pos = f"{ra:.4f}_{dec:.4f}_{epoch:.1f}_{radius:.1f}"
        tasks: dict[str, asyncio.Task[dict[str, Any]]] = {
            "simbad": asyncio.create_task(self._kept("simbad_" + pos, lambda: self._simbad(ra, dec, epoch, radius))),
            "gaia": asyncio.create_task(self._kept("gaia_" + pos, lambda: self._gaia(ra, dec, epoch, radius))),
            "vsx": asyncio.create_task(self._kept("vsx_" + pos, lambda: self._vsx(ra, dec, radius))),
            "ned": asyncio.create_task(self._kept("ned_" + pos, lambda: self._ned(ra, dec, radius))),
        }
        out: dict[str, Any] = {"ra": ra, "dec": dec, "epoch": epoch, "radius": radius}
        gaia = await tasks["gaia"]
        pm: tuple[float | None, float | None] = (None, None)
        if gaia.get("state") == "ok":
            pm = (gaia.get("pmra"), gaia.get("pmdec"))
        else:
            sm = await tasks["simbad"]
            if sm.get("state") == "ok":
                pm = (sm.get("pmra"), sm.get("pmdec"))

        def at(year: float) -> tuple[float, float]:
            return (ra, dec) if pm[0] is None else moved(ra, dec, pm[0], pm[1], year - epoch)

        for part, (_, _, year, _) in IRSA_PARTS.items():
            a, d = at(year)

            def fetch(part: str = part, a: float = a, d: float = d) -> Awaitable[dict[str, Any]]:
                return self._irsa(part, a, d, radius)

            tasks[part] = asyncio.create_task(self._kept(f"{part}_{a:.5f}_{d:.5f}_{radius:.1f}", fetch))
        for k, t in tasks.items():
            out[k] = await t
        out["complete"] = all(out[k].get("state") != "unavailable" for k in tasks)
        out["secs"] = round(time.time() - t0, 2)
        return out

    async def close(self) -> None:
        await self.client.aclose()
