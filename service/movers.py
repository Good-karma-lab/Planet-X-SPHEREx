"""The search between passes of a finished field and the catalogue check of its tracks (docs/plan/02-methods/2.5-searches.md 2.5.2).

The search runs on the white stacks the field already published (no new reads from the archive); then every track is checked
against Gaia DR3 at the dates of the passes:

    same     a Gaia star with proper motion > 2 mas/yr, moved to each pass date, within 3″ of the track in every pass: named, drawn
    still    a Gaia star within 6″ whose proper motion is far below the track's rate: the shift is a centroid effect; counted, not drawn
    none     no Gaia star within 6″: drawn, "no Gaia motion" (unexplained is not new: a brown dwarf, a distant body or an artefact)
    pending  Gaia did not answer: drawn dashed, checked again on the next request
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from scipy.ndimage import center_of_mass, label

from engine import movers
from engine.grid import Grid
from service import cpu, jsonsafe
from service.products.store import Store, product_key
from service.products.sxp import decode
from service.upstream import gaia, gaia_hats
from service.upstream.breaker import Breaker, BreakerOpenError
from service.workers import fieldjob

KIND = "movers"
GAIA_TAP = (gaia.TAP,)
GAIA_EPOCH = gaia.EPOCH
SAME_ARCSEC = 3.0
GAIA_EPOCH_YEAR = 2016.0
SAT_THRESHOLD = 0.3
NEAR_ARCSEC = 6.0
PAIR_ARCSEC = 4.0  # a static star within this of the first and of the last place of a track: the light moves between two stars
PM_MIN = 2.0  # mas/yr
STILL_FRACTION = 0.3  # a star moving less than this share of the track's rate explains nothing
CHECK_AGAIN_S = 60.0


MOVER_SIGMA = 5.0  # a "mover" is a motion measured to at least this many sigma ...
MOVER_SHIFT_ARCSEC = 6.15  # ... that also shifts the source over the baseline by at least one SPHEREx pixel (what the eye can see)
RMS_FLOOR_ARCSEC = 0.3  # the scatter of a short track says little: the error of the speed never counts less than this


def motion_grade(speed: float, rms_arcsec: float, mjds: list[float]) -> dict[str, Any]:
    """How much a measured motion is worth: the speed's error (scatter about the straight line over √Σ(t − t̄)², years), its significance,
    the shift over the baseline of the passes, and the grade: "mover" when it is at least MOVER_SIGMA sigma and at least one pixel
    (MOVER_SHIFT_ARCSEC) over the baseline, else "slow" (a possible slow motion below what a picture can show)."""
    yrs = [m / 365.25 for m in mjds]
    mean = sum(yrs) / max(1, len(yrs))
    ss = sum((y - mean) ** 2 for y in yrs)
    err = max(rms_arcsec, RMS_FLOOR_ARCSEC) / math.sqrt(ss) if ss > 0 else float("inf")
    sigma = speed / err if err > 0 else 0.0
    baseline = (max(yrs) - min(yrs)) if yrs else 0.0
    shift = speed * baseline
    ok = sigma >= MOVER_SIGMA and shift >= MOVER_SHIFT_ARCSEC
    return {
        "speed_err": round(err, 2) if math.isfinite(err) else None,
        "sigma": round(sigma, 1),
        "shift_arcsec": round(shift, 1),
        "baseline_years": round(baseline, 2),
        "grade": "mover" if ok else "slow",
    }


def year_of(mjd: float) -> float:
    return 2000.0 + (mjd - 51544.5) / 365.25


@dataclass
class Run:
    key: str
    field_key: str
    state: str = "running"  # running | done | failed
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    checked_at: float = 0.0


class Movers:
    def __init__(self, store: Store, store_dir: Path) -> None:
        self.store = store
        self.dir = store_dir / "movers"
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.catalogue: Any = None  # the catalogue context (service.context): names the saturated tracks
        self.gaia = Breaker("Gaia archive")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10), headers={"User-Agent": "spherex-sky/0.1"})

    def key_of(self, field_key: str) -> str:
        return product_key(KIND, {"field": field_key})

    def get_or_start(self, field_key: str) -> Run:
        key = self.key_of(field_key)
        run = self.runs.get(key)
        if run is None:
            path = self.dir / f"{key}.json"
            if path.is_file():
                run = Run(key, field_key, state="done", result=json.loads(path.read_text()))
            else:
                run = Run(key, field_key)
                asyncio.get_running_loop().create_task(self._run(run))
            self.runs[key] = run
            while len(self.runs) > 200:
                self.runs.popitem(last=False)
        elif (
            run.state == "done"
            and any(t["check"] == "pending" for t in run.result.get("tracks", []))
            and time.monotonic() - run.checked_at > CHECK_AGAIN_S
        ):
            run.checked_at = time.monotonic()
            asyncio.get_running_loop().create_task(self._check_all(run))
        return run

    async def _run(self, run: Run) -> None:
        try:
            meta = self.store.read_meta(fieldjob.KIND, run.field_key)
            if meta is None:
                raise ValueError("the field is not finished")
            raws = await asyncio.to_thread(self._quick_parts, run.field_key, meta)
            result = await cpu.run(search_parts, run.field_key, meta, raws)
            try:  # bright fast stars saturate: the catalogue names them and the saturated zones give their motion
                stars = await self._fast_stars(meta)
                extra = await cpu.run(saturated_movers, meta, raws, stars, result["tracks"]) if stars else []
            except (BreakerOpenError, httpx.HTTPError, ValueError, KeyError, OSError):
                extra = []  # the catalogue did not answer: the search's own tracks stand, and this part is tried on the next search
            for t in extra:  # the star's name (SIMBAD: "eps Ind A"), when the catalogue answers in a few seconds
                t["name"] = await self._star_name(t)
            result["tracks"] = replace_saturated(list(result["tracks"]), extra, stars if extra else []) + extra
            result["saturated_movers"] = len(extra)
            run.result = result
            run.checked_at = time.monotonic()
            await self._check_all(run)
            run.state = "done"
        except Exception as e:  # say why; the next request tries again
            run.state, run.error = "failed", str(e)[:300]
            self.runs.pop(run.key, None)

    def _quick_parts(self, field_key: str, meta: dict[str, Any]) -> list[bytes]:
        raws = []
        for p in meta["passes"]:
            raw = self.store.read_part(fieldjob.KIND, field_key, f"quick_p{p['index']}.sxp")
            if raw is None:
                raise ValueError("a pass of the field is missing")
            raws.append(raw)
        return raws

    def _search(self, field_key: str, meta: dict[str, Any]) -> dict[str, Any]:
        return search_parts(field_key, meta, self._quick_parts(field_key, meta))

    async def _check_all(self, run: Run) -> None:
        for t in run.result.get("tracks", []):
            if t["check"] == "pending":
                await self._check(t)
        run.result["still"] = sum(1 for t in run.result["tracks"] if t["check"] == "still")
        if all(t["check"] != "pending" for t in run.result["tracks"]):
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{run.key}.part"
            tmp.write_text(jsonsafe.dumps(run.result, separators=(",", ":")))
            tmp.replace(self.dir / f"{run.key}.json")

    async def _star_name(self, t: dict[str, Any]) -> str | None:
        """SIMBAD's name for a catalogue-named track (its display name, e.g. "eps Ind A"), or None."""
        if self.catalogue is None:
            return None
        try:
            r = await asyncio.wait_for(self.catalogue.simbad_at(t["ra_2025_9"], t["dec_2025_9"], 2025.9, 6.0), 6.0)
        except (TimeoutError, httpx.HTTPError, BreakerOpenError, ValueError):
            return None
        if r.get("state") != "ok":
            return None
        name = r.get("display_name") or r.get("name")
        return str(name) if name else None

    async def _fast_stars(self, meta: dict[str, Any]) -> list[dict[str, Any]]:
        """The bright fast stars of the field in Gaia DR3 (G < SAT_STAR_G, proper motion above SAT_PM_MAS), from the archive or, when it
        does not answer, from the Parquet copy on AWS. The cone covers the field and the way a star can have come (11″/yr to the passes)."""
        g = meta["grid"]
        radius = g["n"] * g["step_arcsec"] / 2.0 * math.sqrt(2.0) + 11.0 * 12.0
        q = (
            f"SELECT source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag FROM {gaia.TABLE} "
            f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {g['ra0']:.7f}, {g['dec0']:.7f}, {radius / 3600.0:.7f})) "
            f"AND phot_g_mean_mag < {SAT_STAR_G} AND (pmra*pmra + pmdec*pmdec) > {SAT_PM_MAS**2:.0f}"
        )
        try:
            self.gaia.before()
            try:
                r = await self.client.post(GAIA_TAP[0], data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": q})
                r.raise_for_status()
                body = r.json()
            except Exception:
                self.gaia.failure()
                raise
            self.gaia.success()
            names = [c["name"] for c in body["metadata"]]
            return [dict(zip(names, row, strict=True)) for row in body["data"]]
        except (BreakerOpenError, httpx.HTTPError, ValueError, KeyError):
            rows = await cpu.run(gaia_fast_rows, g["ra0"], g["dec0"], radius / 3600.0)
            return rows

    async def _gaia_near(self, ra: float, dec: float, radius_arcsec: float) -> list[dict[str, Any]]:
        q = (
            f"SELECT source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag FROM {gaia.TABLE} "
            f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {radius_arcsec / 3600.0:.7f}))"
        )
        try:
            self.gaia.before()
            try:
                r = await self.client.post(GAIA_TAP[0], data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": q})
                r.raise_for_status()
                body = r.json()
            except Exception:
                self.gaia.failure()
                raise
            self.gaia.success()
            names = [c["name"] for c in body["metadata"]]
            return [dict(zip(names, row, strict=True)) for row in body["data"]]
        except (BreakerOpenError, httpx.HTTPError, ValueError, KeyError):
            # the Gaia archive did not answer: the same catalogue from the public Parquet copy on AWS
            return await asyncio.to_thread(gaia_hats.cone, ra, dec, radius_arcsec / 3600.0)

    async def _check(self, t: dict[str, Any]) -> None:
        """Compare the track with Gaia stars near it: positions moved from Gaia's epoch to every pass date."""
        years = [year_of(m) for m in t["mjd"]]
        # the track's place at Gaia's epoch, on its fitted line, is the centre of the cone; the radius covers the motion's uncertainty
        dt = GAIA_EPOCH - years[0]
        dec_g = t["dec"][0] + t["v_north"] * dt / 3600.0
        ra_g = t["ra"][0] + t["v_east"] * dt / 3600.0 / math.cos(math.radians(dec_g))
        radius = NEAR_ARCSEC + 0.2 * t["speed"] * abs(dt)
        try:
            stars = await self._gaia_near(ra_g % 360.0, dec_g, radius)
        except (BreakerOpenError, httpx.HTTPError, ValueError, KeyError):
            t["check"] = "pending"
            return
        best = None
        for s in stars:
            if s["pmra"] is None or s["pmdec"] is None:
                continue
            pm = math.hypot(s["pmra"], s["pmdec"])
            if pm < PM_MIN:
                continue
            seps = []
            for ra_t, dec_t, y in zip(t["ra"], t["dec"], years, strict=True):
                k = y - GAIA_EPOCH
                d = s["dec"] + s["pmdec"] * k / 3.6e6
                a = s["ra"] + s["pmra"] * k / 3.6e6 / math.cos(math.radians(d))
                seps.append(math.hypot((a - ra_t) * math.cos(math.radians(d)), d - dec_t) * 3600.0)
            if max(seps) < SAME_ARCSEC and (best is None or max(seps) < best[1]):
                best = (s, max(seps))
        if best is not None:
            s = best[0]
            t["check"] = "same"
            t["gaia"] = {
                "source_id": str(s["source_id"]),
                "pm": round(math.hypot(s["pmra"], s["pmdec"]) / 1000.0, 3),
                "pa": round(math.degrees(math.atan2(s["pmra"], s["pmdec"])) % 360.0, 1),
                "g": None if s["phot_g_mean_mag"] is None else round(s["phot_g_mean_mag"], 2),
                "offset_arcsec": round(best[1], 2),
            }
            return
        # a star near the track's middle place that hardly moves: the "motion" is a centroid effect
        mid = len(t["ra"]) // 2
        for s in stars:
            pm = math.hypot(s["pmra"] or 0.0, s["pmdec"] or 0.0) / 1000.0
            k = years[mid] - GAIA_EPOCH
            d = s["dec"] + (s["pmdec"] or 0.0) * k / 3.6e6
            a = s["ra"] + (s["pmra"] or 0.0) * k / 3.6e6 / math.cos(math.radians(d))
            sep = math.hypot((a - t["ra"][mid]) * math.cos(math.radians(d)), d - t["dec"][mid]) * 3600.0
            if sep < NEAR_ARCSEC and pm < STILL_FRACTION * t["speed"]:
                t["check"] = "still"
                t["gaia"] = {"source_id": str(s["source_id"]), "pm": round(pm, 3), "offset_arcsec": round(sep, 2)}
                return
        # the light moves between two static stars (a variable star in a close pair, or two stars whose brightness ratio changed): a static
        # Gaia star stands at the track's first place and a different one at its last (found in their own small cones: a fast track's
        # middle is far from where its Gaia-epoch cone looks)
        try:
            ends = [await self._static_near(t, k, years[k]) for k in (0, len(t["ra"]) - 1)]
        except (BreakerOpenError, httpx.HTTPError, ValueError, KeyError):
            t["check"] = "pending"
            return
        if ends[0] is not None and ends[1] is not None and ends[0][0]["source_id"] != ends[1][0]["source_id"]:
            t["check"] = "still"
            t["gaia"] = {
                "source_id": str(ends[0][0]["source_id"]),
                "pair": [str(ends[0][0]["source_id"]), str(ends[1][0]["source_id"])],
                "pm": round(max(ends[0][1], ends[1][1]), 3),
                "offset_arcsec": round(max(ends[0][2], ends[1][2]), 2),
            }
            return
        t["check"] = "none"

    async def _static_near(self, t: dict[str, Any], k: int, year: float) -> tuple[dict[str, Any], float, float] | None:
        """The nearest Gaia star within PAIR_ARCSEC of the track's place in pass `k` that hardly moves (less than STILL_FRACTION of the
        track's rate): (star, its proper motion in arcsec/yr, separation)."""
        best: tuple[dict[str, Any], float, float] | None = None
        for s in await self._gaia_near(t["ra"][k], t["dec"][k], PAIR_ARCSEC + 2.0):
            pm = math.hypot(s["pmra"] or 0.0, s["pmdec"] or 0.0) / 1000.0
            if pm >= STILL_FRACTION * t["speed"]:
                continue
            dy = year - GAIA_EPOCH
            d = s["dec"] + (s["pmdec"] or 0.0) * dy / 3.6e6
            a = s["ra"] + (s["pmra"] or 0.0) * dy / 3.6e6 / math.cos(math.radians(d))
            sep = math.hypot((a - t["ra"][k]) * math.cos(math.radians(d)), d - t["dec"][k]) * 3600.0
            if sep <= PAIR_ARCSEC and (best is None or sep < best[2]):
                best = (s, pm, sep)
        return best

    async def check_track(self, t: dict[str, Any]) -> None:
        """The Gaia verdict of a track (positions at every pass: mjd, ra, dec; rate: v_east, v_north, speed): sets check and gaia."""
        await self._check(t)

    def view(self, run: Run) -> dict[str, Any]:
        out: dict[str, Any] = {"key": run.key, "state": run.state, "error": run.error}
        if run.result:
            r = dict(run.result)
            shown: list[dict[str, Any]] = []
            slow: list[dict[str, Any]] = []
            for t in r["tracks"]:
                if t["check"] == "still":
                    continue
                # a motion the catalogue confirms (Gaia "same") is a mover whatever its size; an unexplained one must be measured to
                # MOVER_SIGMA sigma and shift by a pixel over the baseline, else it is only a possible slow motion
                (slow if t.get("grade") == "slow" and t["check"] != "same" else shown).append(t)
            r["tracks"], r["slow_tracks"], r["slow"] = shown, slow, len(slow)
            out["result"] = r
        return out


def search_parts(field_key: str, meta: dict[str, Any], raws: list[bytes]) -> dict[str, Any]:
    """The search between passes on the field's quick parts (runs in a worker process)."""
    whites, sats = [], []
    for raw in raws:
        _, arr = decode(raw)
        whites.append(arr["white"].astype(np.float32))
        sats.append(arr["sat"].astype(np.float32) / 255.0)
    # a pass that covers only a corner of the field (the survey's edge) would leave no place seen by every pass: such passes sit out
    keep = covering_passes(whites)
    dropped = [meta["passes"][i]["index"] for i in range(len(whites)) if i not in keep]
    passes = [meta["passes"][i] for i in keep]
    whites, sats = [whites[i] for i in keep], [sats[i] for i in keep]
    g = meta["grid"]
    grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
    t0 = time.monotonic()
    res = movers.search(whites, sats, [p["mjd_mid"] for p in passes], g["step_arcsec"])
    tracks = []
    for t in res.tracks:
        ra, dec = grid.grid_to_sky([p[0] for p in t.xy], [p[1] for p in t.xy])
        # the place at 2025.9 on the fitted line (x0, y0 is its place at the first pass)
        yrs = 2025.9 - year_of(passes[0]["mjd_mid"])
        ra_e, dec_e = grid.grid_to_sky([t.x0], [t.y0])
        dec_m = float(dec_e[0]) + t.v_north * yrs / 3600.0
        ra_m = float(ra_e[0]) + t.v_east * yrs / 3600.0 / math.cos(math.radians(dec_m))
        tracks.append(
            {
                "passes": [passes[i]["index"] for i in t.passes],
                "mjd": [round(m, 4) for m in t.mjd],
                "ra": [round(float(x), 6) for x in ra],
                "dec": [round(float(x), 6) for x in dec],
                "ra_2025_9": round(ra_m % 360.0, 6),
                "dec_2025_9": round(dec_m, 6),
                "speed": round(t.speed, 2),
                "pa": round(t.pa, 1),
                "v_east": round(t.v_east, 3),
                "v_north": round(t.v_north, 3),
                "rms_arcsec": round(t.rms_arcsec, 2),
                "saturated": t.saturated,
                "snr": [None if not math.isfinite(s) else round(s, 1) for s in t.snr],
                "check": "pending",
                "gaia": None,
                **motion_grade(t.speed, t.rms_arcsec, [float(m) for m in t.mjd]),
            }
        )
    return {
        "field": field_key,
        "passes": len(passes),
        "sources_per_pass": res.sources_per_pass,
        "stationary": res.stationary,
        "weak": res.weak,
        "chance": {"trials": res.chance_trials, "mean": round(res.chance_mean, 3), "any": round(res.chance_any, 3)},
        "passes_left_out": dropped,
        "still": 0,
        "tracks": tracks,
        "seconds": round(time.monotonic() - t0, 2),
    }


SAT_STAR_G = 10.0  # a Gaia star brighter than this saturates SPHEREx
SAT_PM_MAS = 500.0  # and moves at least this fast: the search between passes may lose it (its core is a saturated zone, not a point)
SAT_BLOB_ARCSEC = 12.0  # the saturated zone of a star is looked for this close to where the catalogue puts the star
SAT_MATCH_ARCSEC = 8.0  # its centre may differ from the catalogue place by this much (the zone is a few detector pixels wide)
SAT_MIN_PASSES = 2


def gaia_fast_rows(ra: float, dec: float, radius_deg: float) -> list[dict[str, Any]]:
    """The bright fast Gaia stars around a place from the Parquet copy on AWS (a worker process)."""
    rows = gaia_hats.cone(ra, dec, radius_deg)
    return [
        r
        for r in rows
        if r.get("phot_g_mean_mag") is not None
        and r["phot_g_mean_mag"] < SAT_STAR_G
        and math.hypot(r.get("pmra") or 0.0, r.get("pmdec") or 0.0) > SAT_PM_MAS
    ]


def _carried(st: dict[str, Any], mjd: float) -> tuple[float, float]:
    """A Gaia star (2016 place, proper motion in mas/yr) at the date of a pass."""
    k = year_of(mjd) - GAIA_EPOCH_YEAR
    d = st["dec"] + (st["pmdec"] or 0.0) * k / 3.6e6
    return st["ra"] + (st["pmra"] or 0.0) * k / 3.6e6 / math.cos(math.radians(d)), d


def stands_for(tr: dict[str, Any], st: dict[str, Any]) -> bool:
    """Whether a track passes within 6 arcsec of a Gaia star (carried to the pass dates) in every pass of the track."""
    return all(
        math.hypot(
            (tr["ra"][j] - _carried(st, m)[0]) * math.cos(math.radians(tr["dec"][j])),
            tr["dec"][j] - _carried(st, m)[1],
        )
        * 3600.0
        < 6.0
        for j, m in enumerate(tr["mjd"])
    )


def biased_halo(tr: dict[str, Any], st: dict[str, Any]) -> bool:
    """A track of the search through saturated pixels (not a saturated-zone track) whose speed is not the star's own: the halo's
    centroid."""
    if not tr.get("saturated") or tr.get("source") == "saturated":
        return False
    pm = math.hypot(st["pmra"] or 0.0, st["pmdec"] or 0.0) / 1000.0
    return abs(float(tr["speed"]) - pm) > max(0.5, 0.15 * pm)


def replace_saturated(tracks: list[dict[str, Any]], extra: list[dict[str, Any]], stars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The search's own tracks without the saturated ones that a saturated-zone track now stands for (one star, one track)."""
    named = {str(t["gaia"]["source_id"]) for t in extra if t.get("gaia")}
    by_id = [st for st in stars if str(st["source_id"]) in named]
    return [t for t in tracks if not any(stands_for(t, st) and biased_halo(t, st) for st in by_id)]


def saturated_movers(
    meta: dict[str, Any], raws: list[bytes], stars: list[dict[str, Any]], tracks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fast bright stars that saturate: Gaia's stars of the field (2016 place and motion, `stars`) carried to every pass and looked for as a
    saturated zone of the stacks there. The zone's centre in every pass is measured (the speed is then the zone's own, with its
    scatter); the catalogue says which star it is. A star that a track of the search between passes already stands for is left out.
    -> tracks in the shape of the search's (source "saturated": the places are the measured centres of the saturated zones, the verdict is
    Gaia's)."""
    whites, sats = [], []
    for raw in raws:
        _, arr = decode(raw)
        whites.append(arr["white"].astype(np.float32))
        sats.append(arr["sat"].astype(np.float32) / 255.0)
    keep = covering_passes(whites)
    passes = [meta["passes"][i] for i in keep]
    sats = [sats[i] for i in keep]
    g = meta["grid"]
    grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
    n, step = g["n"], g["step_arcsec"]
    out: list[dict[str, Any]] = []
    for st in stars:
        exp_xy, exp_ra, exp_dec = [], [], []
        for p in passes:
            k = year_of(p["mjd_mid"]) - GAIA_EPOCH_YEAR
            d = st["dec"] + (st["pmdec"] or 0.0) * k / 3.6e6
            a = st["ra"] + (st["pmra"] or 0.0) * k / 3.6e6 / math.cos(math.radians(d))
            gx, gy = grid.sky_to_grid([a], [d])
            exp_xy.append((float(gx[0]), float(gy[0])))
            exp_ra.append(a)
            exp_dec.append(d)
        if any(not (0 <= x < n and 0 <= y < n) for x, y in exp_xy):
            continue
        # a track of the search that stands for the star leaves it out, unless that track is itself a saturated centroid: its speed is
        # the halo's (ε Indi A in a 60′ field came out at 6.9″/yr against Gaia's 4.7 and with no name), the saturated zone is better
        if any(stands_for(tr, st) and not biased_halo(tr, st) for tr in tracks):
            continue
        found: list[tuple[int, float, float]] = []
        rad = max(2, math.ceil(SAT_BLOB_ARCSEC / step))
        for i, (x, y) in enumerate(exp_xy):
            y0, y1, x0, x1 = max(0, int(y) - rad), min(n, int(y) + rad + 1), max(0, int(x) - rad), min(n, int(x) + rad + 1)
            win = sats[i][y0:y1, x0:x1] > SAT_THRESHOLD
            if not win.any():
                continue
            lab, nlab = label(win)
            best: tuple[float, float, float] | None = None
            for blob in range(1, nlab + 1):
                cy, cx = center_of_mass(lab == blob)
                dist = math.hypot(cx + x0 - x, cy + y0 - y) * step
                if best is None or dist < best[0]:
                    best = (dist, float(cx + x0), float(cy + y0))
            if best is not None and best[0] <= SAT_MATCH_ARCSEC:
                found.append((i, best[1], best[2]))
        if len(found) < SAT_MIN_PASSES:
            continue
        idx = [i for i, _, _ in found]
        ra_m, dec_m = grid.grid_to_sky([x for _, x, _ in found], [y for _, _, y in found])
        t_yr = np.array([year_of(passes[i]["mjd_mid"]) for i in idx])
        cosd = math.cos(math.radians(float(np.mean(dec_m))))
        ye, yn = (np.asarray(ra_m) - ra_m[0]) * cosd * 3600.0, (np.asarray(dec_m) - dec_m[0]) * 3600.0
        fe, fn = np.polyfit(t_yr, ye, 1), np.polyfit(t_yr, yn, 1)
        rms = float(np.sqrt(np.mean((ye - np.polyval(fe, t_yr)) ** 2 + (yn - np.polyval(fn, t_yr)) ** 2)))
        ve, vn = float(fe[0]), float(fn[0])
        cat_ve, cat_vn = (st["pmra"] or 0.0) / 1000.0, (st["pmdec"] or 0.0) / 1000.0
        offs = [
            math.hypot((ra_m[j] - exp_ra[i]) * math.cos(math.radians(exp_dec[i])), dec_m[j] - exp_dec[i]) * 3600.0
            for j, i in enumerate(idx)
        ]
        gmag = st.get("g", st.get("phot_g_mean_mag"))
        yrs25 = 2025.9 - year_of(passes[idx[0]]["mjd_mid"])
        out.append(
            {
                "passes": [passes[i]["index"] for i in idx],
                "mjd": [round(passes[i]["mjd_mid"], 4) for i in idx],
                "ra": [round(float(a), 6) for a in ra_m],
                "dec": [round(float(d), 6) for d in dec_m],
                "ra_2025_9": round((float(ra_m[0]) + ve * yrs25 / 3600.0 / cosd) % 360.0, 6),
                "dec_2025_9": round(float(dec_m[0]) + vn * yrs25 / 3600.0, 6),
                "speed": round(math.hypot(ve, vn), 2),
                "pa": round(math.degrees(math.atan2(ve, vn)) % 360.0, 1),
                "v_east": round(ve, 3),
                "v_north": round(vn, 3),
                "rms_arcsec": round(rms, 2),
                "saturated": True,
                "snr": [None] * len(idx),
                "check": "same",
                "gaia": {
                    "source_id": str(st["source_id"]),
                    "pm": round(math.hypot(cat_ve, cat_vn), 3),
                    "pa": round(math.degrees(math.atan2(cat_ve, cat_vn)) % 360.0, 1),
                    "g": None if gmag is None else round(float(gmag), 2),
                    "offset_arcsec": round(max(offs), 2),
                },
                "source": "saturated",
                **motion_grade(math.hypot(ve, vn), rms, [passes[i]["mjd_mid"] for i in idx]),
                "note": "a bright star: its core is saturated; the places are the centres of the saturated zone, the name is Gaia's",
            }
        )
    return out


def covering_passes(whites: list[np.ndarray], share: float = 0.5) -> list[int]:
    """The passes that cover at least `share` of what the best-covering pass covers."""
    cover = [float(np.isfinite(w).mean()) for w in whites]
    best = max(cover, default=0.0)
    return [i for i, c in enumerate(cover) if best > 0 and c >= share * best]
