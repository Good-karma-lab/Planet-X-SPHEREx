"""The spectrum of a place, measured live (docs/plan/02-methods/2.4-spectra.md 2.4.1, 2.4.3).

A click asks for the spectrum of one sky position (optionally with a proper motion, for a moving star). Every exposure that covers the
place is measured by PSF photometry (engine/photometry.py) at the object's position at that exposure's time; each point is published
the moment it is fitted, so the page draws the spectrum while it grows. A finished spectrum is kept on disk and served at once.

Reads are I/O-bound and the fit of a 7 × 7 stamp takes a fraction of a millisecond, so a spectrum runs in the service's event loop with
a bounded number of exposures in flight, not in the worker processes that build fields.
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

from engine import photometry
from engine.spx import flags as spxflags
from engine.spx import psf as spxpsf
from engine.spx import wave as spxwave
from engine.spx import wcs as spxwcs
from indexer.store import Exposure, Index
from service.products.store import product_key
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.psfs import PsfProvider

KIND = "spectrum"
VERSION = "s1"
RADIUS_DEG = 20.0 / 3600.0  # exposures whose footprint (with its margin) holds the place
IN_FLIGHT = 48
HALF = photometry.HALF
RAW = 7  # the window of file pixels kept for the page: 15 × 15 around the source pixel
SATURATED_SHARE = 0.5  # more than this share of saturated cores: "no SPHEREx spectrum"
KEEP_RUNS = 200


def _arr(a: np.ndarray) -> list[list[float | None]]:
    """A small array for JSON: 4 significant decimals, None where there is no value."""
    return [[None if not np.isfinite(v) else round(float(v), 4) for v in row] for row in a]


def place_at(params: dict[str, Any], mjd: float) -> tuple[float, float]:
    """The object's position at a time: the J2000 place moved by its proper motion (mas/yr; zero for a fixed place)."""
    ra, dec = float(params["ra"]), float(params["dec"])
    pmra, pmdec = float(params.get("pmra") or 0.0), float(params.get("pmdec") or 0.0)
    if pmra == 0.0 and pmdec == 0.0:
        return ra, dec
    years = (mjd - 51544.5) / 365.25 - (float(params.get("epoch", 2000.0)) - 2000.0)
    d = dec + pmdec * years / 3.6e6
    r = ra + pmra * years / 3.6e6 / max(0.05, math.cos(math.radians((d + dec) / 2)))
    return r % 360.0, d


def spectrum_params(ra: float, dec: float, pmra: float = 0.0, pmdec: float = 0.0, epoch: float = 2000.0) -> dict[str, Any]:
    p: dict[str, Any] = {"ra": round(ra % 360.0, 6), "dec": round(dec, 6)}
    if pmra or pmdec:
        p.update(pmra=round(pmra, 3), pmdec=round(pmdec, 3), epoch=round(epoch, 3))
    return p


@dataclass
class Run:
    key: str
    params: dict[str, Any]
    total: int = 0
    points: list[dict[str, Any]] = field(default_factory=list)
    stamps: dict[int, dict[str, Any]] = field(default_factory=dict)
    catalogue: dict[str, Any] | None = None
    state: str = "running"  # running | done | failed
    error: str = ""
    started: float = field(default_factory=time.monotonic)
    task: asyncio.Task[None] | None = None

    def stamps_view(self, since: int = 0) -> dict[str, Any]:
        """The pixels of every exposure from index `since` on: status 0 nothing read, 1 file pixels only, 2 fitted (with the 7 × 7 data,
        model = flux × PSF + sky, residual and the pixels used)."""
        rows = [{"i": k, **self.stamps[k]} for k in sorted(self.stamps) if k >= since]
        return {
            "key": self.key,
            "state": self.state,
            "total": self.total,
            "since": since,
            "raw_half": RAW,
            "fit_half": HALF,
            "stamps": rows,
        }

    def view(self, since: int = 0) -> dict[str, Any]:
        sat = sum(1 for p in self.points if p["why"] == "saturated core")
        usable = sum(1 for p in self.points if p["ok"])
        return {
            "key": self.key,
            "params": self.params,
            "state": self.state,
            "error": self.error,
            "total": self.total,
            "measured": len(self.points),
            "usable": usable,
            "saturated": sat,
            "no_spectrum": self.state == "done" and self.total > 0 and sat > SATURATED_SHARE * self.total,
            "since": since,
            "points": self.points[since:],
        }


class Spectra:
    def __init__(self, index: Index, files: FileReader, wavelengths: WavelengthMaps, store_dir: Path) -> None:
        self.index = index
        self.files = files
        self.wavelengths = wavelengths
        self.psfs = PsfProvider(files, store_dir)
        self.dir = store_dir / "spectra" / VERSION
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.sem = asyncio.Semaphore(IN_FLIGHT)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), headers={"User-Agent": "spherex-sky/0.1"})

    def key_of(self, params: dict[str, Any]) -> str:
        return product_key(KIND, params)

    def get(self, params: dict[str, Any]) -> Run | None:
        key = self.key_of(params)
        if key in self.runs:
            self.runs.move_to_end(key)
            return self.runs[key]
        path = self.dir / f"{key}.json"
        if path.is_file():
            d = json.loads(path.read_text())
            run = Run(key, params, total=d["total"], points=d["points"], state="done")
            sp = self.dir / f"{key}.stamps.json"
            if sp.is_file():
                run.stamps = {int(k): v for k, v in json.loads(sp.read_text()).items()}
            self._keep(run)
            return run
        return None

    def start(self, params: dict[str, Any]) -> Run:
        run = Run(self.key_of(params), params)
        self._keep(run)
        run.task = asyncio.get_running_loop().create_task(self._run(run))
        return run

    def _keep(self, run: Run) -> None:
        self.runs[run.key] = run
        while len(self.runs) > KEEP_RUNS:
            old_key, old = next(iter(self.runs.items()))
            if old.state == "running":
                break
            del self.runs[old_key]

    async def _run(self, run: Run) -> None:
        try:
            exps = await asyncio.to_thread(self.index.overlapping, float(run.params["ra"]), float(run.params["dec"]), RADIUS_DEG)
            run.total = len(exps)
            await asyncio.gather(*(self._one(run, i, e) for i, e in enumerate(exps)))
            ok = [p for p in run.points if p["ok"]]
            if ok:
                lam = np.array([p["lam"] for p in ok])
                flux = np.array([p["flux"] for p in ok])
                for p, bad in zip(ok, photometry.neighbour_outliers(lam, flux), strict=True):
                    if bad:
                        p["ok"], p["why"] = False, "deviates from its neighbours"
            run.points.sort(key=lambda p: p["i"])
            run.state = "done"
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{run.key}.part"
            tmp.write_text(json.dumps({"params": run.params, "total": run.total, "points": run.points}, separators=(",", ":")))
            tmp.replace(self.dir / f"{run.key}.json")
            tmp = self.dir / f"{run.key}.stamps.part"
            tmp.write_text(json.dumps({str(k): v for k, v in run.stamps.items()}, separators=(",", ":")))
            tmp.replace(self.dir / f"{run.key}.stamps.json")
        except Exception as e:  # the place could not be measured: say so; the next request starts again
            run.state, run.error = "failed", str(e)[:300]
            self.runs.pop(run.key, None)

    async def _one(self, run: Run, i: int, e: Exposure) -> None:
        async with self.sem:
            try:
                p = await self.measure(run.params, e)
            except Exception as ex:  # one unreadable exposure is one missing point, never a failed spectrum
                p = {"ok": False, "why": f"not readable ({type(ex).__name__})"}
            st = p.pop("_stamp", {"status": 0})
            run.stamps[i] = st
            run.points.append({"i": i, "mjd": round(e.mjd0, 5), "det": e.det, "release": e.release, **p})

    async def measure(self, params: dict[str, Any], e: Exposure) -> dict[str, Any]:
        lay = await self.files.layout(e.key)
        w = self.files.wcs_of(e.key, lay)
        ra_t, dec_t = place_at(params, lay.mjd)
        px, py = spxwcs.sky2pix(w, [ra_t], [dec_t])
        x, y = float(px[0]), float(py[0])
        nx, ny = lay.image.nx, lay.image.ny
        if not (np.isfinite(x) and np.isfinite(y)) or x < HALF or y < HALF or x > nx - 1 - HALF or y > ny - 1 - HALF:
            return {"ok": False, "why": "at the edge of the detector"}
        xi, yi = round(x), round(y)
        # the 15 × 15 file pixels around the source (no value beyond the detector's edge), for the page; the fit uses the inner 7 × 7
        ry0, ry1, rx0, rx1 = max(0, yi - RAW), min(ny, yi + RAW + 1), max(0, xi - RAW), min(nx, xi + RAW + 1)
        raw = np.full((2 * RAW + 1, 2 * RAW + 1), np.nan)
        raw[ry0 - (yi - RAW) : ry1 - (yi - RAW), rx0 - (xi - RAW) : rx1 - (xi - RAW)] = (await self.files.rows(e.key, "image", ry0, ry1))[
            :, rx0:rx1
        ].astype(np.float64)
        y0, y1, x0, x1 = yi - HALF, yi + HALF + 1, xi - HALF, xi + HALF + 1
        img = raw[RAW - HALF : RAW + HALF + 1, RAW - HALF : RAW + HALF + 1].copy()
        flg = (await self.files.rows(e.key, "flags", y0, y1))[:, x0:x1].astype(np.int64)
        var = (await self.files.rows(e.key, "variance", y0, y1))[:, x0:x1].astype(np.float64)
        table, _, _ = spxflags.resolve(lay.flags_header)
        moving = bool(params.get("pmra") or params.get("pmdec"))
        bad_names = [n for n in spxflags.BAD_ALWAYS if not (moving and n in spxflags.MOVER_KEEP)]
        bad = (flg & spxflags.mask_of(table, bad_names)) != 0
        overflow = (flg & spxflags.mask_of(table, spxflags.SATURATION)) != 0
        saturated = overflow | (~np.isfinite(img) & ~bad)
        usable = np.isfinite(img) & ~bad & ~overflow
        ps = await self.psfs.for_file(e.key)
        fx, fy = x - xi, y - yi
        psf = spxpsf.stamp(ps, ps.zone_index(x, y), fx, fy, HALF)
        # the pixel's solid angle from the local WCS (sr)
        cra, cdec = spxwcs.pix2sky(w, [x - 0.5, x + 0.5, x, x], [y, y, y - 0.5, y + 0.5])
        cosd = math.cos(math.radians(dec_t))
        dx = np.array([((cra[1] - cra[0] + 540) % 360 - 180) * cosd, cdec[1] - cdec[0]])
        dy = np.array([((cra[3] - cra[2] + 540) % 360 - 180) * cosd, cdec[3] - cdec[2]])
        omega = abs(dx[0] * dy[1] - dx[1] * dy[0]) * (math.pi / 180.0) ** 2
        lam = await self._wavelength(e.key, lay.detector, xi, yi)
        pt = photometry.fit(img, var, usable, saturated, psf, fx, fy, omega)
        out: dict[str, Any] = {"lam": round(lam, 5), "ok": pt.ok, "why": pt.why, "x": round(x, 2), "y": round(y, 2)}
        stamp: dict[str, Any] = {"status": 1, "raw": _arr(raw)}
        if pt.ok and pt.flux_mjy is not None and pt.err_mjy is not None:
            out.update(flux=round(pt.flux_mjy, 5), err=round(pt.err_mjy, 5), chi2=None if pt.chi2 is None else round(pt.chi2, 2))
            if pt.amp is not None and pt.sky is not None and pt.used is not None:
                model = pt.amp * psf + pt.sky
                stamp.update(
                    status=2,
                    sky=round(pt.sky, 4),
                    flux=round(pt.flux_mjy, 5),
                    data=_arr(img),
                    model=_arr(model),
                    resid=_arr(img - model),
                    used=pt.used.astype(int).tolist(),
                )
        out["_stamp"] = stamp
        return out

    async def _wavelength(self, key: str, det: int, x: int, y: int) -> float:
        try:
            cw, _ = await self.wavelengths.get(det)
            v = float(cw[y, x])
            if np.isfinite(v) and v > 0:
                return v
        except Exception:  # the calibration map could not be read: the file's own table
            pass
        wh, wd = await self.files.table_bytes(key, "wave")
        lam, _ = spxwave.WaveMap(spxwave.table_from_hdu_bytes(wh, wd), det)([float(x)], [float(y)])
        return float(lam[0])
