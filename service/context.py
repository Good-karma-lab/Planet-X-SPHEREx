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

from service import cpu
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
SIMBAD_HEDGE_S = 1.0  # the second SIMBAD mirror is asked as well when the first has not answered by then
CONTEXT_PARTS = ("simbad", "gaia", "vsx", "ned", "twomass", "allwise", "catwise")
IDENT_FAST_MAS = 1000.0  # above this proper motion (mas/yr) a star can have left the small cone: SIMBAD is asked for those in a wide cone
GAIA_HEDGE_S = 1.0  # the AWS copy is asked as well when the Gaia archive has not answered after this long
IDENT_DEADLINE_S = (
    3.0  # a passport waits this long for one catalogue, then shows it as still being asked (the answer is kept for the next ask)
)
BRIGHT_MAG = 9.0  # a star brighter than this saturates SPHEREx: the centre of its core is uncertain by several arcseconds
BRIGHT_RADIUS = 15.0
BRIGHT_IR_MAG = 7.0  # a 2MASS or WISE magnitude brighter than this saturates the survey: its place is uncertain there too
BRIGHT_NOTE = "a bright star: its core is saturated, so the place is only good to several arcseconds"
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


def _fnum(row: dict[str, Any], key: str) -> float | None:
    v = row.get(key)
    if v in (None, "", "null"):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def parse_simbad(rows: list[list[Any]], ra: float, dec: float, epoch: float, radius: float) -> dict[str, Any]:
    """The nearest SIMBAD object at `epoch` (its proper motion applied), preferring one with a magnitude; when none is inside `radius`, a
    bright star (brighter than BRIGHT_MAG: saturated, its core's centre uncertain) inside BRIGHT_RADIUS."""
    best: tuple[float, dict[str, Any]] | None = None
    bright: tuple[float, dict[str, Any]] | None = None
    for r in rows:
        a, d = moved(r[1], r[2], r[3], r[4], epoch - SIMBAD_EPOCH)
        sep = sep_arcsec(a, d, ra, dec)
        mag = mag_shown(r[9], r[10], r[11])
        score = sep + (0.0 if mag else 2.0)
        found = {
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
            "j": None if r[11] is None else round(float(r[11]), 2),
        }
        if sep <= radius and (best is None or score < best[0]):
            best = (score, found)
        elif sep <= BRIGHT_RADIUS and mag is not None and mag["value"] < BRIGHT_MAG and (bright is None or sep < bright[0]):
            bright = (sep, {**found, "note": BRIGHT_NOTE})
    if best:
        return best[1]
    return bright[1] if bright else {"state": "none"}


def gaia_rows(body: str) -> list[dict[str, Any]]:
    """The rows of a Gaia TAP answer (CSV)."""
    return list(csv.DictReader(io.StringIO(body)))


def parse_gaia(body: str, ra: float, dec: float, epoch: float, radius: float, label: str) -> dict[str, Any]:
    return parse_gaia_rows(gaia_rows(body), ra, dec, epoch, radius, label)


def parse_gaia_rows(rows: list[dict[str, Any]], ra: float, dec: float, epoch: float, radius: float, label: str) -> dict[str, Any]:
    """The Gaia star nearest to (ra, dec) at `epoch` (its proper motion applied to its 2016 place); when none is inside `radius`, a bright
    star (G < BRIGHT_MAG: saturated in SPHEREx) inside BRIGHT_RADIUS."""
    best: tuple[float, dict[str, Any]] | None = None
    bright: tuple[float, dict[str, Any]] | None = None
    for row in rows:
        a0, d0 = _fnum(row, "ra"), _fnum(row, "dec")
        if a0 is None or d0 is None:
            continue
        pa, pd = _fnum(row, "pmra") or 0.0, _fnum(row, "pmdec") or 0.0
        a, d = moved(a0, d0, pa, pd, epoch - GAIA_EPOCH)
        sep = sep_arcsec(a, d, ra, dec)
        g = _fnum(row, "phot_g_mean_mag")
        is_bright = g is not None and g < BRIGHT_MAG
        if sep > radius and not (is_bright and sep <= BRIGHT_RADIUS):
            continue
        bp, rp = _fnum(row, "phot_bp_mean_mag"), _fnum(row, "phot_rp_mean_mag")
        bprp = _fnum(row, "bp_rp") if "bp_rp" in row else (bp - rp if bp is not None and rp is not None else None)
        found = {
            "state": "ok",
            "source_id": str(row["source_id"]),
            "g": g,
            "bp_rp": None if bprp is None else round(bprp, 3),
            "parallax": _fnum(row, "parallax"),
            "parallax_err": _fnum(row, "parallax_error"),
            "ruwe": _fnum(row, "ruwe"),
            "pmra": pa,
            "pmdec": pd,
            "variable": row.get("phot_variable_flag") if "phot_variable_flag" in row else None,
            "sep": round(sep, 2),
            "source": label,
        }
        if sep <= radius:
            if best is None or sep < best[0]:
                best = (sep, found)
        elif bright is None or sep < bright[0]:
            bright = (sep, {**found, "note": BRIGHT_NOTE})
    if best:
        return best[1]
    return bright[1] if bright else {"state": "none", "source": label}


def _ir_saturated(mags: list[Any]) -> bool:
    """A near-infrared or mid-infrared magnitude bright enough that the survey's own place for the star is uncertain (it saturates)."""
    return any(isinstance(m, int | float) and m < BRIGHT_IR_MAG for m in mags)


def parse_irsa(body: str, ra: float, dec: float, radius: float, labels: dict[str, str], bright: bool = False) -> dict[str, Any]:
    """The nearest entry within `radius`; with `bright` (the place is a bright, saturated star) also a bright entry up to BRIGHT_RADIUS."""
    best: tuple[float, dict[str, Any]] | None = None
    for row in csv.DictReader(io.StringIO(body)):
        a, d = _fnum(row, "ra"), _fnum(row, "dec")
        if a is None or d is None:
            continue
        sep = sep_arcsec(a, d, ra, dec)
        vals = {k: (row.get(c) if k in TEXT_KEYS else _fnum(row, c)) for k, c in labels.items()}
        far = sep > radius + 1.0
        if far and not (bright and sep <= BRIGHT_RADIUS and _ir_saturated([vals.get(k) for k in ("J", "H", "Ks", "W1")])):
            continue
        if best is None or sep < best[0]:
            best = (sep, {"state": "ok", "sep": round(sep, 2), **vals, **({"note": BRIGHT_NOTE} if far else {})})
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


def allwise_cone(ra: float, dec: float, radius: float) -> list[dict[str, Any]]:
    """The AllWISE sources around a place from the Parquet copy in AWS (a worker process: it reads files over the network)."""
    from engine.extras import allwise

    return allwise.sources(ra, dec, radius)


def from_allwise(part: str, rows: list[dict[str, Any]], radius: float, bright: bool = False) -> dict[str, Any] | None:
    """The answer of `parse_irsa` for 2MASS or AllWISE built from rows of the AllWISE Parquet copy (marked `via`); None when the copy cannot
    say (2MASS: a source stands there but AllWISE has no 2MASS match for it, which does not mean 2MASS has none)."""
    via = "AllWISE copy in AWS (IRSA's TAP did not answer)"
    for r in rows:
        far = r["sep_arcsec"] > radius + 1.0
        if far and not (
            bright
            and r["sep_arcsec"] <= BRIGHT_RADIUS
            and _ir_saturated([r.get("w1mpro"), r.get("j_m_2mass"), r.get("h_m_2mass"), r.get("k_m_2mass")])
        ):
            continue
        note = {"note": BRIGHT_NOTE} if far else {}
        if part == "allwise":
            return {
                **note,
                "state": "ok",
                "sep": r["sep_arcsec"],
                "designation": r.get("designation"),
                "W1": r.get("w1mpro"),
                "W2": r.get("w2mpro"),
                "W3": r.get("w3mpro"),
                "W4": r.get("w4mpro"),
                "cc_flags": r.get("cc_flags"),
                "via": via,
            }
        if r.get("j_m_2mass") is not None or r.get("k_m_2mass") is not None:
            return {
                **note,
                "state": "ok",
                "sep": r["sep_arcsec"],
                "designation": None,
                "J": r.get("j_m_2mass"),
                "H": r.get("h_m_2mass"),
                "Ks": r.get("k_m_2mass"),
                "qual": None,
                "via": via + ", the 2MASS magnitudes of AllWISE's own association",
            }
    reach = (BRIGHT_RADIUS if bright else radius) + 1.0
    if part == "twomass" and any(r["sep_arcsec"] <= reach for r in rows):
        return None
    return {"state": "none", "via": via}


def gaia_cloud_rows(ra: float, dec: float, radius_arcsec: float) -> list[dict[str, Any]]:
    """Gaia DR3 rows around a place from the Parquet copy in AWS (a worker process)."""
    from service.upstream import gaia_hats

    return gaia_hats.cone(ra, dec, radius_arcsec / 3600.0)


def display_name(main_id: str, aliases: list[str]) -> str:
    """The name to show: SIMBAD's main identifier without its type prefix, but the primary of a system ("eps Ind A") when SIMBAD names the
    object by the system ("* eps Ind") and another of its names says it is component A ("GJ 845 A")."""
    plain = main_id.removeprefix("V* ").removeprefix("* ").removeprefix("NAME ").strip()
    if len(plain) > 1 and plain[-2] == " " and plain[-1].isupper():
        return plain  # already a component (eps Ind B)
    for a in aliases:  # the object has a name of its own as component A of a system ("GJ 845 A"): it is the primary
        a = a.removeprefix("* ").strip()
        if len(a) > 2 and a.endswith(" A") and a != plain:
            return plain + " A"
    return plain


class CatalogueContext:
    """The seven catalogues for a place, asked together, each behind its own breaker, cached for a week (answers 'ok' and 'none' only)."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(DEADLINE_S, connect=4.0), headers={"User-Agent": "spherex-sky/0.1"})
        self.breakers = {k: Breaker(k) for k in ("simbad", "simbad2", "gaia", "irsa", "vizier", "ned")}
        self.cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._background: set[asyncio.Task[None]] = set()

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

        async def ask(which: str, base: str) -> dict[str, Any]:
            body = await self._get(which, base, {"request": "doQuery", "lang": "adql", "format": "json", "query": adql})
            found = parse_simbad(json.loads(body).get("data") or [], ra, dec, epoch, radius)
            if found.get("state") == "ok":  # the name people use (eps Ind A) beside SIMBAD's main identifier
                aliases = await self._aliases(base, found["name"], which)
                found["aliases"] = aliases
                found["display_name"] = display_name(found["name"], aliases)
            return found

        # Two mirrors, each with its own breaker: the second is asked as well when the first fails or has not answered within a second, and
        # the first answer wins, so one mirror being down or slow never costs the name.
        first = asyncio.create_task(ask("simbad", SIMBAD_BASES[0]))
        second: asyncio.Task[dict[str, Any]] | None = None
        running: set[asyncio.Task[dict[str, Any]]] = {first}
        err: Exception | None = None
        try:
            while running:
                done, running = await asyncio.wait(
                    running, timeout=SIMBAD_HEDGE_S if second is None else None, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    try:
                        return t.result()
                    except (httpx.HTTPError, BreakerOpenError, ValueError) as e:
                        err = e
                if second is None:
                    second = asyncio.create_task(ask("simbad2", SIMBAD_BASES[1]))
                    running.add(second)
        finally:
            for left in (first, second):
                if left is not None and not left.done():
                    left.cancel()
        raise err or RuntimeError("no SIMBAD mirror")

    async def _aliases(self, base: str, main_id: str, which: str = "simbad") -> list[str]:
        """The other names SIMBAD has for an object (a few, best effort: the answer does not wait on them)."""
        q = (
            "SELECT TOP 40 ident.id FROM ident JOIN basic ON ident.oidref = basic.oid WHERE basic.main_id = '"
            + main_id.replace("'", "''")
            + "'"
        )
        try:
            body = await asyncio.wait_for(self._get(which, base, {"request": "doQuery", "lang": "adql", "format": "json", "query": q}), 2.0)
            return [str(r[0]) for r in (json.loads(body).get("data") or [])]
        except (httpx.HTTPError, BreakerOpenError, ValueError, TimeoutError):
            return []

    async def _gaia(self, ra: float, dec: float, epoch: float, radius: float) -> dict[str, Any]:
        """Gaia DR3 at `epoch`: the stars in a small cone (every star that moves less than a few arcseconds a year) and, in a wide cone, the
        fast ones (above IDENT_FAST_MAS), each moved from 2016 to the epoch. The archives are asked in turn; when none answers, the same
        catalogue is read from the Parquet copy in AWS, so the answer is never "nothing" just because a server was down."""
        yrs = abs(epoch - GAIA_EPOCH)
        small = (radius + BRIGHT_RADIUS + IDENT_FAST_MAS / 1000.0 * yrs + 1.0) / 3600.0
        big = (radius + BRIGHT_RADIUS + IDENT_MAX_MAS / 1000.0 * yrs + 1.0) / 3600.0
        cols = {
            "gaiadr3.gaia_source": (
                "source_id, ra, dec, pmra, pmdec, parallax, parallax_error, phot_g_mean_mag, bp_rp, ruwe, phot_variable_flag"
            ),
            "gaia.dr3lite": (
                "source_id, ra, dec, pmra, pmdec, parallax, parallax_error, phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag, ruwe"
            ),
        }

        async def archives() -> dict[str, Any]:
            err: Exception | None = None
            for url, table, label in GAIA_TAPS:
                where = (
                    f"(1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {small:.6f}))) OR "
                    f"(1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {big:.6f})) "
                    f"AND (pmra*pmra + pmdec*pmdec) > {IDENT_FAST_MAS**2:.0f})"
                )
                adql = f"SELECT TOP 1500 {cols[table]} FROM {table} WHERE {where}"
                try:
                    return parse_gaia(await self._tap("gaia", url, adql), ra, dec, epoch, radius, label)
                except (httpx.HTTPError, BreakerOpenError) as e:
                    err = e
            raise err or RuntimeError("no Gaia archive")

        async def cloud() -> dict[str, Any]:  # the Parquet copy in AWS (a worker process: it reads files over the network)
            rows = await cpu.run(gaia_cloud_rows, ra, dec, big * 3600.0)
            return parse_gaia_rows(rows, ra, dec, epoch, radius, "Gaia DR3 on AWS (the archive was slow or did not answer)")

        # the archive first; when it has not answered after GAIA_HEDGE_S (or fails) the AWS copy is asked as well, and the first answer wins
        first = asyncio.ensure_future(archives())
        try:
            return await asyncio.wait_for(asyncio.shield(first), GAIA_HEDGE_S)
        except (httpx.HTTPError, BreakerOpenError, TimeoutError):
            pass
        second = asyncio.ensure_future(cloud())
        pending: set[asyncio.Future[dict[str, Any]]] = {first, second}
        last: BaseException | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    exc = t.exception()
                    if exc is None:
                        return t.result()
                    last = exc
        finally:
            for t in pending:
                t.cancel()
        raise last or RuntimeError("no Gaia archive")

    async def gaia_at(self, ra: float, dec: float, epoch: float = 2025.9, radius: float = 6.0) -> dict[str, Any]:
        """Gaia alone, kept the same way as inside `context`."""
        pos = f"{ra:.4f}_{dec:.4f}_{epoch:.1f}_{radius:.1f}"
        return await self._kept("gaia2_" + pos, lambda: self._gaia(ra, dec, epoch, radius))

    async def _within(self, task: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        """A catalogue's answer, or `pending` when it has not come in IDENT_DEADLINE_S (it keeps running; the next ask finds it kept)."""
        fut = asyncio.ensure_future(task)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), IDENT_DEADLINE_S)
        except TimeoutError:
            return {"state": "pending", "error": "still being asked"}

    async def identify(self, ra: float, dec: float, epoch: float = 2025.9, radius: float = 6.0, only: str | None = None) -> dict[str, Any]:
        """What SIMBAD and Gaia DR3 call the place where a source stands at `epoch`, in the shape of the `identify` route. Positions of the
        catalogues are carried to that epoch with their proper motions, whatever the star's motion; SIMBAD and Gaia are asked together, each
        for at most IDENT_DEADLINE_S (`pending` after that, the answer arrives for the next ask), and a catalogue that fails is
        `unavailable`, never `none`. `only` = "simbad" or "gaia" asks one of them (the page asks both and shows each as it arrives)."""
        want_sm, want_gm = only in (None, "simbad"), only in (None, "gaia")
        sm: dict[str, Any] = {"state": "skipped"}
        gm: dict[str, Any] = {"state": "skipped"}
        if want_sm and want_gm:
            sm, gm = await asyncio.gather(
                self._within(self.simbad_at(ra, dec, epoch, radius)), self._within(self.gaia_at(ra, dec, epoch, radius))
            )
        elif want_sm:
            sm = await self._within(self.simbad_at(ra, dec, epoch, radius))
        else:
            gm = await self._within(self.gaia_at(ra, dec, epoch, radius))
        out: dict[str, Any] = {}
        if sm.get("state") == "ok":
            out["simbad"] = {
                "state": "ok",
                "name": sm["name"],
                "display_name": sm.get("display_name") or sm["name"],
                "aliases": sm.get("aliases") or [],
                "type": sm.get("otype"),
                "sp": sm.get("sptype"),
                "sep": sm.get("sep"),
                "pmra": sm.get("pmra"),
                "pmdec": sm.get("pmdec"),
                **({"note": sm["note"]} if "note" in sm else {}),
            }
        elif sm.get("state") != "skipped":
            out["simbad"] = {k: sm[k] for k in ("state", "error") if k in sm}
        if gm.get("state") == "ok":
            out["gaia"] = {
                k: gm.get(k)
                for k in ("state", "source_id", "g", "bp_rp", "parallax", "ruwe", "pmra", "pmdec", "sep", "source", "note")
                if gm.get(k) is not None or k in ("state", "source_id")
            }
        elif gm.get("state") != "skipped":
            out["gaia"] = {k: gm[k] for k in ("state", "error") if k in gm}
        return out

    async def _irsa(
        self, part: str, ra: float, dec: float, radius: float, alt: tuple[float, float] | None = None, bright: bool = False
    ) -> dict[str, Any]:
        """One IRSA catalogue at the place `ra, dec` (at that catalogue's epoch). When IRSA's TAP does not answer, 2MASS and AllWISE are
        read from IRSA's Parquet copy of AllWISE in AWS (it carries AllWISE's 2MASS magnitudes): `alt` is the place at AllWISE's epoch."""
        table, cols, _, labels = IRSA_PARTS[part]
        cone = ((BRIGHT_RADIUS if bright else radius) + 1.0) / 3600.0
        adql = (
            f"SELECT TOP 10 {cols} FROM {table} WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {cone:.6f})) = 1"
        )
        try:
            return parse_irsa(await self._tap("irsa", IRSA_TAP, adql), ra, dec, radius, labels, bright)
        except (httpx.HTTPError, BreakerOpenError, ValueError) as first:
            if part not in ("twomass", "allwise"):
                raise
            a, d = (ra, dec) if part == "allwise" or alt is None else alt
            try:
                rows = await cpu.run(allwise_cone, a, d, (BRIGHT_RADIUS if bright else radius) + 1.0)
            except Exception:  # the cloud copy did not answer either: the first failure is the one said
                raise first from None
            answer = from_allwise(part, rows, radius, bright)
            if answer is None:
                raise first
            return answer

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

    async def simbad_at(self, ra: float, dec: float, epoch: float = 2025.9, radius: float = 6.0) -> dict[str, Any]:
        """SIMBAD alone (the same answer, kept the same way, as inside `context`)."""
        pos = f"{ra:.4f}_{dec:.4f}_{epoch:.1f}_{radius:.1f}"
        return await self._kept("simbad_" + pos, lambda: self._simbad(ra, dec, epoch, radius))

    async def context(self, ra: float, dec: float, epoch: float = 2025.9, radius: float = 6.0) -> dict[str, Any]:
        out: dict[str, Any] = {}
        await self._context(ra, dec, epoch, radius, out)
        return out

    async def context_within(self, ra: float, dec: float, epoch: float, radius: float, deadline: float) -> dict[str, Any]:
        """`context`, but at most `deadline` seconds: the catalogues that have not answered are `pending` (still being asked; they keep
        running and the next call finds their answers kept)."""
        out: dict[str, Any] = {}
        work = asyncio.create_task(self._context(ra, dec, epoch, radius, out))
        self._background.add(work)
        work.add_done_callback(self._background.discard)
        done, _ = await asyncio.wait({work}, timeout=deadline)
        if done:
            work.result()
            return out
        snap = dict(out)
        for k in CONTEXT_PARTS:
            if k not in snap:
                snap[k] = {"state": "pending", "error": "still being asked"}
        snap["complete"] = False
        return snap

    async def _context(self, ra: float, dec: float, epoch: float, radius: float, out: dict[str, Any]) -> None:
        t0 = time.time()
        pos = f"{ra:.4f}_{dec:.4f}_{epoch:.1f}_{radius:.1f}"
        out.update({"ra": ra, "dec": dec, "epoch": epoch, "radius": radius})
        tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}

        def start(k: str, t: asyncio.Task[dict[str, Any]]) -> None:
            tasks[k] = t

            def land(done: asyncio.Task[dict[str, Any]], k: str = k) -> None:
                if not done.cancelled() and done.exception() is None:
                    out[k] = done.result()

            t.add_done_callback(land)

        start("simbad", asyncio.create_task(self._kept("simbad_" + pos, lambda: self._simbad(ra, dec, epoch, radius))))
        start("gaia", asyncio.create_task(self._kept("gaia_" + pos, lambda: self._gaia(ra, dec, epoch, radius))))
        start("vsx", asyncio.create_task(self._kept("vsx_" + pos, lambda: self._vsx(ra, dec, radius))))
        start("ned", asyncio.create_task(self._kept("ned_" + pos, lambda: self._ned(ra, dec, radius))))
        gaia = await tasks["gaia"]
        pm: tuple[float | None, float | None] = (None, None)
        sm = await tasks["simbad"]
        if gaia.get("state") == "ok":
            pm = (gaia.get("pmra"), gaia.get("pmdec"))
        elif sm.get("state") == "ok":
            pm = (sm.get("pmra"), sm.get("pmdec"))
        # a bright (saturated) star: the infrared surveys' own places for it are uncertain too, so they are asked in the wide cone
        bright = any(a.get("state") == "ok" and a.get("note") == BRIGHT_NOTE for a in (gaia, sm))

        def at(year: float) -> tuple[float, float]:
            return (ra, dec) if pm[0] is None else moved(ra, dec, pm[0], pm[1], year - epoch)

        for part, (_, _, year, _) in IRSA_PARTS.items():
            a, d = at(year)
            alt = at(2010.5)

            def fetch(part: str = part, a: float = a, d: float = d, alt: tuple[float, float] = alt) -> Awaitable[dict[str, Any]]:
                return self._irsa(part, a, d, radius, alt, bright)

            start(part, asyncio.create_task(self._kept(f"{part}_{a:.5f}_{d:.5f}_{radius:.1f}{'b' if bright else ''}", fetch)))
        for k, t in tasks.items():
            out[k] = await t
        two, sb = out.get("twomass") or {}, out.get("simbad") or {}
        if two.get("state") == "unavailable" and sb.get("state") == "ok" and sb.get("j") is not None:
            # IRSA did not answer: SIMBAD's own J magnitude is 2MASS's, for the star it names (a star that moves fast has no AllWISE match)
            out["twomass"] = {
                "state": "ok",
                "sep": sb["sep"],
                "designation": None,
                "J": sb["j"],
                "H": "—",
                "Ks": "—",
                "qual": None,
                "via": "SIMBAD's 2MASS J magnitude of " + str(sb["name"]) + " (the 2MASS catalogue did not answer)",
                **({"note": sb["note"]} if "note" in sb else {}),
            }
        out["complete"] = all(out[k].get("state") != "unavailable" for k in tasks)
        out["secs"] = round(time.time() - t0, 2)

    async def close(self) -> None:
        await self.client.aclose()
