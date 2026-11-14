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

from engine import movers
from engine.grid import Grid
from service.products.store import Store, product_key
from service.products.sxp import decode
from service.upstream.breaker import Breaker, BreakerOpenError
from service.workers import fieldjob

KIND = "movers"
GAIA_TAP = ("https://gea.esac.esa.int/tap-server/tap/sync",)
GAIA_EPOCH = 2016.0
SAME_ARCSEC = 3.0
NEAR_ARCSEC = 6.0
PM_MIN = 2.0  # mas/yr
STILL_FRACTION = 0.3  # a star moving less than this share of the track's rate explains nothing
CHECK_AGAIN_S = 60.0


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
            result = await asyncio.to_thread(self._search, run.field_key, meta)
            run.result = result
            run.checked_at = time.monotonic()
            await self._check_all(run)
            run.state = "done"
        except Exception as e:  # say why; the next request tries again
            run.state, run.error = "failed", str(e)[:300]
            self.runs.pop(run.key, None)

    def _search(self, field_key: str, meta: dict[str, Any]) -> dict[str, Any]:
        passes = meta["passes"]
        whites, sats = [], []
        for p in passes:
            raw = self.store.read_part(fieldjob.KIND, field_key, f"quick_p{p['index']}.sxp")
            if raw is None:
                raise ValueError("a pass of the field is missing")
            _, arr = decode(raw)
            whites.append(arr["white"].astype(np.float32))
            sats.append(arr["sat"].astype(np.float32) / 255.0)
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
                    "passes": t.passes,
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
                }
            )
        return {
            "field": field_key,
            "passes": len(passes),
            "sources_per_pass": res.sources_per_pass,
            "stationary": res.stationary,
            "weak": res.weak,
            "chance": {"trials": res.chance_trials, "mean": round(res.chance_mean, 3), "any": round(res.chance_any, 3)},
            "still": 0,
            "tracks": tracks,
            "seconds": round(time.monotonic() - t0, 2),
        }

    async def _check_all(self, run: Run) -> None:
        for t in run.result.get("tracks", []):
            if t["check"] == "pending":
                await self._check(t)
        run.result["still"] = sum(1 for t in run.result["tracks"] if t["check"] == "still")
        if all(t["check"] != "pending" for t in run.result["tracks"]):
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{run.key}.part"
            tmp.write_text(json.dumps(run.result, separators=(",", ":")))
            tmp.replace(self.dir / f"{run.key}.json")

    async def _gaia_near(self, ra: float, dec: float, radius_arcsec: float) -> list[dict[str, Any]]:
        q = (
            "SELECT source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag FROM gaiadr3.gaia_source "
            f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {radius_arcsec / 3600.0:.7f}))"
        )
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
        t["check"] = "none"

    def view(self, run: Run) -> dict[str, Any]:
        out: dict[str, Any] = {"key": run.key, "state": run.state, "error": run.error}
        if run.result:
            r = dict(run.result)
            r["tracks"] = [t for t in r["tracks"] if t["check"] != "still"]
            out["result"] = r
        return out
