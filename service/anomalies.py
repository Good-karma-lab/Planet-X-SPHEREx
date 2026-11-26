"""The spectral anomaly search of a finished field, started by "Find spectral anomalies here" (docs/plan/02-methods/2.6-anomalies-
checks-chance.md 2.6.1), and the passport of each anomaly.

Sources: the stationary sources of the search between passes with S/N ≥ 12 (the brightest 300; saturated ones left out) and every
track of that search (measured along it). Every exposure of the field (at most 800) is opened once and all sources are measured from
it by PSF photometry; the noise of a stamp is the robust scatter of the exposure inside the field (the variance plane is not read: one
read less per exposure, and the 5 % floor of the score dominates for every source bright enough to have a spectrum). The spectra are
then binned and scored against the field's own ordinary star (engine/anomaly.py). The anomalies are identified in SIMBAD and Gaia DR3.
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

from engine import anomaly, movers, photometry
from engine.grid import Grid
from engine.spx import flags as spxflags
from engine.spx import psf as spxpsf
from engine.spx import wcs as spxwcs
from indexer.store import Exposure, Index
from service import cpu, jsonsafe
from service.movers import covering_passes
from service.products.store import Store, product_key
from service.products.sxp import decode
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.psfs import PsfProvider, psf_from_path
from service.spectrum import place_at
from service.upstream import gaia, gaia_hats
from service.upstream.breaker import Breaker, BreakerOpenError
from service.workers import fieldjob

KIND = "anomalies"
MIN_SNR = 12.0
MAX_SOURCES = 300
MAX_EXPOSURES = 800
IN_FLIGHT = 12
HALF = photometry.HALF
GAIA_TAP = gaia.TAP
SIMBAD_TAP = "https://simbad.harvard.edu/simbad/sim-tap/sync"
IDENTIFY_ARCSEC = 6.0


def _year(mjd: float) -> float:
    return 2000.0 + (mjd - 51544.5) / 365.25


@dataclass
class Run:
    key: str
    field_key: str
    state: str = "running"  # running | done | failed
    phase: str = "finding the sources"
    done: int = 0
    total: int = 0
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)


class Anomalies:
    def __init__(self, index: Index, files: FileReader, wavelengths: WavelengthMaps, store: Store, store_dir: Path) -> None:
        self.index = index
        self.files = files
        self.wavelengths = wavelengths
        self.store = store
        self.psfs = PsfProvider(files, store_dir)
        self.dir = store_dir / "anomalies"
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.sem = asyncio.Semaphore(IN_FLIGHT)
        self.breakers = {"gaia": Breaker("Gaia archive"), "simbad": Breaker("SIMBAD")}
        self.catalogue: Any = None  # the catalogue context (service.context): when set, it answers `identify`
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), headers={"User-Agent": "spherex-sky/0.1"})

    def key_of(self, field_key: str) -> str:
        return product_key(KIND, {"field": field_key})

    def get(self, field_key: str) -> Run | None:
        key = self.key_of(field_key)
        if key in self.runs:
            return self.runs[key]
        path = self.dir / f"{key}.json"
        if path.is_file():
            run = Run(key, field_key, state="done", phase="done", result=json.loads(path.read_text()))
            self.runs[key] = run
            return run
        return None

    def start(self, field_key: str) -> Run:
        run = Run(self.key_of(field_key), field_key)
        self.runs[run.key] = run
        while len(self.runs) > 100:
            k, old = next(iter(self.runs.items()))
            if old.state == "running":
                break
            del self.runs[k]
        asyncio.get_running_loop().create_task(self._run(run))
        return run

    def view(self, run: Run) -> dict[str, Any]:
        return {
            "key": run.key,
            "state": run.state,
            "phase": run.phase,
            "done": run.done,
            "total": run.total,
            "error": run.error,
            "result": run.result or None,
        }

    # ---- the job ---------------------------------------------------------------------------------------------------------------
    async def _run(self, run: Run) -> None:
        try:
            meta = self.store.read_meta(fieldjob.KIND, run.field_key)
            if meta is None:
                raise ValueError("the field is not finished")
            g = meta["grid"]
            grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
            raws = await asyncio.to_thread(self._quick_parts, run.field_key, meta)
            sources = await cpu.run(field_sources, meta, grid, raws)
            radius = g["n"] * g["step_arcsec"] / 3600.0 / 2.0 * math.sqrt(2.0)
            exps = await asyncio.to_thread(self.index.overlapping, g["ra0"], g["dec0"], radius)
            if len(exps) > MAX_EXPOSURES:
                pick = np.linspace(0, len(exps) - 1, MAX_EXPOSURES).round().astype(int)
                exps = [exps[i] for i in pick]
            run.total = len(exps)
            run.phase = f"measuring {len(sources)} sources in every exposure"
            points: list[list[tuple[float, float]]] = [[] for _ in sources]

            async def one(e: Exposure) -> None:
                async with self.sem:
                    try:
                        for s_i, lam, flux in await self._measure(e, sources):
                            points[s_i].append((lam, flux))
                    except Exception:  # an unreadable exposure is missing points, never a failed search
                        pass
                    run.done += 1

            await asyncio.gather(*(one(e) for e in exps))
            run.phase = "comparing every spectrum with the ordinary star of the field"
            result = await cpu.run(score_sources, sources, points)
            run.phase = "asking SIMBAD and Gaia about the anomalies"
            for a in result["anomalies"]:
                a["identified"] = await self.identify(a["ra"], a["dec"], a.get("pmra", 0.0), a.get("pmdec", 0.0))
            result["exposures"] = len(exps)
            result["seconds"] = round(time.monotonic() - run.started, 1)
            run.result = result
            run.state, run.phase = "done", "done"
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{run.key}.part"
            tmp.write_text(jsonsafe.dumps(result, separators=(",", ":")))
            tmp.replace(self.dir / f"{run.key}.json")
        except Exception as e:  # say why; the button starts it again
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

    def _sources(self, field_key: str, meta: dict[str, Any], grid: Grid) -> list[dict[str, Any]]:
        return field_sources(meta, grid, self._quick_parts(field_key, meta))

    async def _measure(self, e: Exposure, sources: list[dict[str, Any]]) -> list[tuple[int, float, float]]:
        lay = await self.files.layout(e.key)
        w = await self.files.wcs(e.key, lay)
        nx, ny = lay.image.nx, lay.image.ny
        places = [place_at(s, lay.mjd) for s in sources]
        px, py = spxwcs.sky2pix(w, [p[0] for p in places], [p[1] for p in places])
        inside = np.isfinite(px) & np.isfinite(py) & (px >= HALF) & (py >= HALF) & (px <= nx - 1 - HALF) & (py <= ny - 1 - HALF)
        if not inside.any():
            return []
        y0 = int(np.floor(py[inside].min())) - HALF
        y1 = int(np.ceil(py[inside].max())) + HALF + 1
        x0 = int(np.floor(px[inside].min())) - HALF
        x1 = int(np.ceil(px[inside].max())) + HALF + 1
        img = (await self.files.rows(e.key, "image", y0, y1))[:, x0:x1].astype(np.float64)
        flg = (await self.files.rows(e.key, "flags", y0, y1))[:, x0:x1].astype(np.int64)
        ps_path = await self.psfs.path_for_file(e.key)
        cw, _ = await self.wavelengths.get(lay.detector)
        table, _, _ = lay.flag_bits
        box = np.ascontiguousarray(cw[y0:y1, x0:x1])  # only the box crosses to the worker, not the detector's whole map
        moves = [bool(s["moves"]) for s in sources]
        return await cpu.run(fit_all, img, flg, table, ps_path, box, px, py, inside, x0, y0, moves)

    # ---- identification ----------------------------------------------------------------------------------------------------------
    async def _tap(self, which: str, url: str, adql: str) -> list[dict[str, Any]]:
        br = self.breakers[which]
        br.before()
        try:
            r = await self.client.post(url, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql})
            r.raise_for_status()
            body = r.json()
        except Exception:
            br.failure()
            raise
        br.success()
        names = [c["name"] for c in body["metadata"]]
        return [dict(zip(names, row, strict=True)) for row in body["data"]]

    async def identify(self, ra: float, dec: float, pmra: float, pmdec: float) -> dict[str, Any]:
        """What SIMBAD and Gaia DR3 call the place (at the survey's epoch for a moving source)."""
        if self.catalogue is not None:
            named: dict[str, Any] = await self.catalogue.identify(ra, dec)
            return named
        r_ep, d_ep = place_at({"ra": ra, "dec": dec, "pmra": pmra, "pmdec": pmdec}, 61000.0)
        rad = (IDENTIFY_ARCSEC + 12.0 * (1 if (pmra or pmdec) else 0)) / 3600.0
        out: dict[str, Any] = {}
        try:
            rows = await self._tap(
                "simbad",
                SIMBAD_TAP,
                "SELECT TOP 5 main_id, otype, sp_type, ra, dec, pmra, pmdec, plx_value FROM basic "
                f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {rad + 30.0 / 3600.0:.7f}))",
            )
            best = None
            for s in rows:
                k = _year(61000.0) - 2000.0
                d = s["dec"] + (s["pmdec"] or 0.0) * k / 3.6e6
                a = s["ra"] + (s["pmra"] or 0.0) * k / 3.6e6 / math.cos(math.radians(d))
                sep = math.hypot((a - r_ep) * math.cos(math.radians(d)), d - d_ep) * 3600.0
                if sep < IDENTIFY_ARCSEC and (best is None or sep < best[1]):
                    best = (s, sep)
            out["simbad"] = (
                {"state": "ok", "name": best[0]["main_id"], "type": best[0]["otype"], "sp": best[0]["sp_type"], "sep": round(best[1], 1)}
                if best
                else {"state": "none"}
            )
        except (BreakerOpenError, httpx.HTTPError, ValueError, KeyError) as e:
            out["simbad"] = {"state": "unavailable", "error": str(e)[:120]}
        try:
            k = gaia.EPOCH - 2000.0
            d16 = dec + pmdec * k / 3.6e6
            r16 = ra + pmra * k / 3.6e6 / math.cos(math.radians(d16))
            try:
                rows = await self._tap(
                    "gaia",
                    GAIA_TAP,
                    "SELECT TOP 5 source_id, ra, dec, pmra, pmdec, parallax, parallax_error, ruwe, phot_g_mean_mag, bp_rp "
                    f"FROM {gaia.TABLE} "
                    f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
                    f"CIRCLE('ICRS', {r16 % 360.0:.7f}, {d16:.7f}, {IDENTIFY_ARCSEC / 3600.0:.7f}))",
                )
            except (BreakerOpenError, httpx.HTTPError, ValueError, KeyError):
                # the Gaia archive did not answer: the same catalogue from the public Parquet copy on AWS
                rows = await asyncio.to_thread(gaia_hats.cone, r16 % 360.0, d16, IDENTIFY_ARCSEC / 3600.0)
            if rows:
                g = min(rows, key=lambda s: math.hypot((s["ra"] - r16) * math.cos(math.radians(d16)), s["dec"] - d16))
                out["gaia"] = {
                    "state": "ok",
                    "source_id": str(g["source_id"]),
                    "g": None if g["phot_g_mean_mag"] is None else round(g["phot_g_mean_mag"], 2),
                    "bp_rp": None if g["bp_rp"] is None else round(g["bp_rp"], 2),
                    "parallax": None if g["parallax"] is None else round(g["parallax"], 2),
                    "ruwe": None if g["ruwe"] is None else round(g["ruwe"], 2),
                }
            else:
                out["gaia"] = {"state": "none"}
        except Exception as e:  # neither the archive nor the Parquet copy answered
            out["gaia"] = {"state": "unavailable", "error": str(e)[:120]}
        return out


def field_sources(meta: dict[str, Any], grid: Grid, raws: list[bytes]) -> list[dict[str, Any]]:
    passes = meta["passes"]
    whites, sats = [], []
    for raw in raws:
        _, arr = decode(raw)
        whites.append(arr["white"].astype(np.float32))
        sats.append(arr["sat"].astype(np.float32) / 255.0)
    keep = covering_passes(whites)
    passes = [passes[i] for i in keep]
    whites, sats = [whites[i] for i in keep], [sats[i] for i in keep]
    mjds = [p["mjd_mid"] for p in passes]
    if len(passes) >= 3:
        res = movers.search(whites, sats, mjds, grid.step_arcsec)
        still = res.still_sources
        tracks = res.tracks
    else:  # two passes: no tracks, and every source of the first pass counts
        still = movers.find_sources(whites[0], sats[0], grid.step_arcsec)
        tracks = []
    still = [s for s in still if not s.saturated and s.snr >= MIN_SNR][:MAX_SOURCES]
    out: list[dict[str, Any]] = []
    if still:
        ra, dec = grid.grid_to_sky([s.x for s in still], [s.y for s in still])
        for s, r, d in zip(still, ra, dec, strict=True):
            out.append({"ra": float(r), "dec": float(d), "snr": s.snr, "moves": False})
    for t in tracks:
        # the track's J2000 place and its motion (mas/yr), so that place_at() puts it where it was on each exposure's date
        r0, d0 = grid.grid_to_sky([t.x0], [t.y0])
        yrs = _year(t.ref_mjd) - 2000.0
        d2000 = float(d0[0]) - t.v_north * yrs / 3600.0
        r2000 = float(r0[0]) - t.v_east * yrs / 3600.0 / math.cos(math.radians(d2000))
        snr = min((x for x in t.snr if math.isfinite(x)), default=1e9)
        out.append({"ra": r2000 % 360.0, "dec": d2000, "pmra": t.v_east * 1000.0, "pmdec": t.v_north * 1000.0, "snr": snr, "moves": True})
    return out


def fit_all(
    img: np.ndarray,
    flg: np.ndarray,
    table: dict[str, int],
    ps_or_path: spxpsf.PsfSet | str,
    cw: np.ndarray,
    px: np.ndarray,
    py: np.ndarray,
    inside: np.ndarray,
    x0: int,
    y0: int,
    moves: list[bool],
) -> list[tuple[int, float, float]]:
    """PSF fits of the sources inside one exposure's box (runs in a worker process; cw is the wavelength map of the box)."""
    ps = psf_from_path(ps_or_path) if isinstance(ps_or_path, str) else ps_or_path
    bad_all = spxflags.mask_of(table, spxflags.BAD_ALWAYS)
    bad_mover = spxflags.mask_of(table, [n for n in spxflags.BAD_ALWAYS if n not in spxflags.MOVER_KEEP])
    overflow = (flg & spxflags.mask_of(table, spxflags.SATURATION)) != 0
    good = np.isfinite(img) & ((flg & bad_all) == 0) & ~overflow
    if good.sum() < 50:
        return []
    v = img[good]
    sigma = 1.4826 * float(np.median(np.abs(v - np.median(v))))
    if not np.isfinite(sigma) or sigma <= 0:
        return []
    out: list[tuple[int, float, float]] = []
    for i in np.nonzero(inside)[0]:
        x, y = float(px[i]), float(py[i])
        xi, yi = round(x), round(y)
        sl = (slice(yi - HALF - y0, yi + HALF + 1 - y0), slice(xi - HALF - x0, xi + HALF + 1 - x0))
        stamp, f = img[sl], flg[sl]
        if stamp.shape != (2 * HALF + 1, 2 * HALF + 1):
            continue
        bad = (f & (bad_mover if moves[i] else bad_all)) != 0
        ov = overflow[sl]
        usable = np.isfinite(stamp) & ~bad & ~ov
        saturated = ov | (~np.isfinite(stamp) & ~bad)
        psf = spxpsf.stamp(ps, ps.zone_index(x, y), x - xi, y - yi, HALF)
        # the solid angle of the pixel enters as a constant: the shape of a spectrum, not its scale, is compared
        pt = photometry.fit(stamp, np.full(stamp.shape, sigma * sigma), usable, saturated, psf, x - xi, y - yi, 1.0e-9)
        if pt.ok and pt.flux_mjy is not None:
            lam = float(cw[yi - y0, xi - x0])
            if np.isfinite(lam) and lam > 0:
                out.append((int(i), lam, pt.flux_mjy))
    return out


def score_sources(sources: list[dict[str, Any]], points: list[list[tuple[float, float]]]) -> dict[str, Any]:
    binned = [
        anomaly.bin_spectrum(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
        if pts
        else anomaly.bin_spectrum(np.zeros(0), np.zeros(0))
        for pts in points
    ]
    brightness = [float(np.nansum(b.med)) for b in binned]
    fs = anomaly.score_field(binned, brightness)
    anomalies = []
    scored = [(i, s) for i, s in enumerate(fs.scores) if s is not None]
    ranked = sorted([x for x in scored if x[1].anomalous], key=lambda x: -x[1].score)
    for n, (i, s) in enumerate(ranked, start=1):
        src = sources[i]
        anomalies.append(
            {
                "n": n,
                "ra": round(src["ra"], 6),
                "dec": round(src["dec"], 6),
                "pmra": round(src.get("pmra", 0.0), 2),
                "pmdec": round(src.get("pmdec", 0.0), 2),
                "moves": src["moves"],
                "snr": None if not math.isfinite(src["snr"]) else round(src["snr"], 1),
                "score": round(s.score, 2),
                "points": binned[i].points,
                "drivers": [{k: round(v, 3) for k, v in d.items()} for d in s.drivers],
                "shape": [None if not np.isfinite(x) else round(float(x), 6) for x in s.shape],
                "shape_err": [None if not np.isfinite(x) else round(float(x), 6) for x in s.shape_err],
                "model": [round(float(x), 6) for x in s.model],
                "resid_z": [None if not np.isfinite(x) else round(float(x), 2) for x in s.resid_z],
            }
        )
    return {
        "sources": len(sources),
        "with_spectrum": fs.family_size,
        "median": None if not math.isfinite(fs.median) else round(fs.median, 3),
        "scatter": None if not math.isfinite(fs.scatter) else round(fs.scatter, 3),
        "threshold": None if not math.isfinite(fs.threshold) else round(fs.threshold, 3),
        "reason": fs.reason,
        "bins": [round(float(x), 4) for x in anomaly.CENTRES],
        "edges": [round(float(x), 4) for x in anomaly.EDGES],
        "scores": sorted([round(s.score, 3) for _, s in scored]),
        "anomalies": anomalies,
    }
