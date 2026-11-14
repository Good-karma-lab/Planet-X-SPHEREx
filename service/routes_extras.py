"""Routes of the extra data sources (`/api/v1/cube/...`, later catalogues, light curves and references).

Added to the app with one line in `create_app`: `routes_extras.register(app)`. The sources use their own S3 clients, so a failing
extra source does not trip the breaker of the Level-2 reads.
"""

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from engine.extras import ades, allwise, cadc_ref
from engine.extras.cloudcat import Reader, separation_arcsec
from engine.extras.cube import PIXEL_SR, CubeSource, RangeReader
from service.reader.s3 import S3, ReadError
from service.spectrum import place_at, spectrum_params
from service.upstream import gaia_hats
from service.upstream.breaker import BreakerOpenError

API_PREFIX = "/api/v1"
CUBE_ROOT = os.environ.get("SKY_CUBE_ROOT", "dr1/hips5_cube/hips_20261026")
GAIA_EPOCH_MJD = 57388.5  # J2016.0
MAX_ROWS = 10
CUBE_CAVEATS = [
    "quick look at the all-sky cube: 12.9″ pixels, one pixel, background-subtracted by the pipeline, not for photometry",
    "stripes at the sky's helium line (1.083 µm), Pa-α, the 3.3 µm PAH band and Br-α come from the sky, not the object",
    "empty channels (NaN) are real gaps: the pipeline wrote none for those channels at this place",
]


class AdesObservation(BaseModel):
    mjd_utc: float = Field(ge=40000, le=90000)
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    rms_ra_arcsec: float = Field(gt=0, le=3600)
    rms_dec_arcsec: float = Field(gt=0, le=3600)
    mag: float | None = None
    rms_mag: float | None = None
    band: str | None = None
    position_km: tuple[float, float, float] | None = None


class AdesRequest(BaseModel):
    track_id: str = Field(max_length=16)
    observations: list[AdesObservation] = Field(min_length=1, max_length=200)
    submitter: str = Field(default="", max_length=120)
    institution: str = Field(default="", max_length=120)
    observers: list[str] = Field(default_factory=list, max_length=10)
    measurers: list[str] = Field(default_factory=list, max_length=10)
    ast_cat: str = Field(default="UNK", max_length=12)
    mode: str = Field(default="UNK", max_length=4)
    comment: str = Field(default="", max_length=200)


class Extras:
    """The sources of the extra routes and the small answer caches in front of them."""

    def __init__(
        self,
        cube_reader: RangeReader | None = None,
        cube_root: str = CUBE_ROOT,
        allwise_reader: Reader | None = None,
        gaia_cone: Callable[[float, float, float], list[dict[str, Any]]] | None = None,
        cadc_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._s3 = cube_reader if cube_reader is not None else S3(max_in_flight=32)
        self.cube = CubeSource(self._s3, cube_root)
        self.cube_cache: OrderedDict[tuple[float, float, str], dict[str, Any]] = OrderedDict()
        self._allwise_reader = allwise_reader
        self._gaia_cone = gaia_cone or gaia_hats.cone
        self.xmatch_cache: OrderedDict[tuple[float, float, int, int, float, str], dict[str, Any]] = OrderedDict()
        self._cadc = cadc_client
        self.reference_cache: OrderedDict[tuple[float, float], dict[str, Any]] = OrderedDict()
        self.cutout_cache: OrderedDict[tuple[str, float, float, float], bytes] = OrderedDict()
        self.artifacts: dict[str, str] = {}

    def cadc(self) -> httpx.AsyncClient:
        if self._cadc is None:
            self._cadc = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), headers={"User-Agent": cadc_ref.USER_AGENT})
        return self._cadc

    async def deep_references(self, ra: float, dec: float) -> dict[str, Any]:
        key = (round(ra, 4), round(dec, 4))
        hit = self.reference_cache.get(key)
        if hit is not None:
            return hit
        try:
            refs = await cadc_ref.find(self.cadc(), ra, dec)
        except Exception as e:  # said, never cached
            return {"state": "unavailable", "error": f"{type(e).__name__}: {str(e)[:120]}", "references": []}
        out: dict[str, Any] = {
            "state": "ok" if refs else "none",
            "min_level": cadc_ref.MIN_LEVEL,
            "min_exposure_s": cadc_ref.MIN_EXPOSURE_S,
            "references": [r.as_dict() for r in refs[:MAX_ROWS]],
        }
        self.reference_cache[key] = out
        if len(self.reference_cache) > 2000:
            self.reference_cache.popitem(last=False)
        return out

    async def deep_cutout(self, publisher_id: str, ra: float, dec: float, size: float) -> bytes:
        key = (publisher_id, round(ra, 5), round(dec, 5), round(size, 1))
        hit = self.cutout_cache.get(key)
        if hit is not None:
            self.cutout_cache.move_to_end(key)
            return hit
        art = self.artifacts.get(publisher_id)
        if art is None:
            art = self.artifacts[publisher_id] = await cadc_ref.artifact(self.cadc(), publisher_id)
        data = await cadc_ref.cutout(self.cadc(), art, ra, dec, size)
        self.cutout_cache[key] = data
        if len(self.cutout_cache) > 60:
            self.cutout_cache.popitem(last=False)
        return data

    async def close(self) -> None:
        if isinstance(self._s3, S3):
            await self._s3.close()
        if self._cadc is not None:
            await self._cadc.aclose()
            self._cadc = None

    async def cube_spectrum(self, ra: float, dec: float, flags: str) -> dict[str, Any]:
        key = (round(ra, 5), round(dec, 5), flags)
        hit = self.cube_cache.get(key)
        if hit is not None:
            self.cube_cache.move_to_end(key)
            return hit
        sp = await self.cube.spectrum(ra, dec, flags)
        if sp is None:
            raise HTTPException(404, "that place falls outside the cube tile (the pole-side edge): try a point a little nearer its centre")
        out: dict[str, Any] = {
            "state": "ok",
            "release": "dr1",
            "ra": ra,
            "dec": dec,
            "tile": sp.tile,
            "pixel": {"x": sp.x, "y": sp.y},
            "pixel_sr": PIXEL_SR,
            "unit": "MJy/sr",
            "flag_names": {str(k): v for k, v in sp.flag_names.items()},
            "points": [
                {"lam": lam, "bw": bw, "det": d, "sb": v, "ok": v is not None, "flags": f}
                for lam, bw, d, v, f in zip(sp.lam, sp.bandwidth, sp.detector, sp.sb, sp.flags, strict=True)
            ],
            "caveats": CUBE_CAVEATS,
        }
        self.cube_cache[key] = out
        if len(self.cube_cache) > 2000:
            self.cube_cache.popitem(last=False)
        return out

    async def _allwise(self, params: dict[str, Any], radius: float) -> dict[str, Any]:
        ra, dec = place_at(params, allwise.EPOCH_MJD)
        rows = await asyncio.to_thread(allwise.sources, ra, dec, radius, self._allwise_reader)
        if not rows:
            return {"state": "none", "epoch": 2010.5, "ra": ra, "dec": dec, "n": 0}
        return {
            "state": "ok",
            "epoch": 2010.5,
            "ra": ra,
            "dec": dec,
            "n": len(rows),
            "nearest": rows[0],
            "photometry": allwise.photometry(rows[0]),
            "rows": rows[:MAX_ROWS],
        }

    async def _gaia(self, params: dict[str, Any], radius: float) -> dict[str, Any]:
        ra, dec = place_at(params, GAIA_EPOCH_MJD)
        found = await asyncio.to_thread(self._gaia_cone, ra, dec, radius / 3600.0)
        if not found:
            return {"state": "none", "epoch": 2016.0, "ra": ra, "dec": dec, "n": 0}
        import numpy as np

        sep = separation_arcsec(np.array([r["ra"] for r in found]), np.array([r["dec"] for r in found]), ra, dec)
        rows = []
        for r, s in sorted(zip(found, sep, strict=True), key=lambda t: float(t[1])):
            row = dict(r)
            row["sep_arcsec"] = round(float(s), 3)
            rows.append(row)
        return {"state": "ok", "epoch": 2016.0, "ra": ra, "dec": dec, "n": len(rows), "nearest": rows[0], "rows": rows[:MAX_ROWS]}

    async def xmatch(self, ra: float, dec: float, pmra: float, pmdec: float, radius: float, cats: str) -> dict[str, Any]:
        """AllWISE (with 2MASS) and Gaia DR3 sources at a place, each moved to the catalogue's epoch; each part ok / none / unavailable."""
        key = (round(ra, 5), round(dec, 5), round(pmra), round(pmdec), radius, cats)
        hit = self.xmatch_cache.get(key)
        if hit is not None:
            self.xmatch_cache.move_to_end(key)
            return hit
        params = spectrum_params(ra, dec, pmra, pmdec)
        wanted = cats.split(",")
        jobs = {"allwise": self._allwise, "gaia": self._gaia}
        names = [c for c in jobs if c in wanted]
        results = await asyncio.gather(*[jobs[c](params, radius) for c in names], return_exceptions=True)
        out: dict[str, Any] = {"radius_arcsec": radius}
        for name, res in zip(names, results, strict=True):
            if isinstance(res, BaseException):  # said, never cached
                out[name] = {"state": "unavailable", "error": f"{type(res).__name__}: {str(res)[:120]}"}
            else:
                out[name] = res
        if all(out[n]["state"] != "unavailable" for n in names):
            self.xmatch_cache[key] = out
            if len(self.xmatch_cache) > 5000:
                self.xmatch_cache.popitem(last=False)
        return out


def build_router(extras: Extras) -> APIRouter:
    router = APIRouter()

    @router.get(f"{API_PREFIX}/cube/spectrum")
    async def cube_spectrum(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        flags: str = Query("bad", pattern="^(bad|all|none)$"),
    ) -> Any:
        """The spectrum of one 12.9″ pixel of the DR1 all-sky cube, read by range requests (about a second)."""
        try:
            return await extras.cube_spectrum(ra, dec, flags)
        except BreakerOpenError as e:
            raise HTTPException(503, str(e), headers={"Retry-After": str(int(e.retry_after_s) + 1)}) from e
        except ReadError as e:
            raise HTTPException(502, f"the cube is not answering: {e}") from e

    @router.get(f"{API_PREFIX}/xmatch")
    async def xmatch(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
        radius: float = Query(3.0, gt=0, le=30),
        cats: str = Query("allwise,gaia", pattern="^(allwise|gaia)(,(allwise|gaia))*$"),
    ) -> Any:
        """Catalogue sources at a place from cloud Parquet (AllWISE + 2MASS magnitudes, Gaia DR3): no TAP service in the path."""
        return await extras.xmatch(ra, dec, pmra, pmdec, radius, cats)

    @router.get(f"{API_PREFIX}/reference/deep")
    async def reference_deep(ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90)) -> Any:
        """Deep (level 3, 300 s or more) optical stacks of CADC that cover a place: CFHT MegaPipe and HST where they exist."""
        return await extras.deep_references(ra, dec)

    async def _cutout(ra: float, dec: float, size: float, pid: str) -> bytes:
        try:
            return await extras.deep_cutout(pid, ra, dec, size)
        except (cadc_ref.CadcError, httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"CADC did not give the cutout: {type(e).__name__}") from e

    @router.get(f"{API_PREFIX}/reference/deep/cutout.fits")
    async def reference_cutout_fits(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        size: float = Query(60.0, ge=1, le=cadc_ref.MAX_CUTOUT_ARCSEC),
        id: str = Query(..., pattern=r"^ivo://cadc\.nrc\.ca/[A-Za-z0-9_./?+-]+$"),
    ) -> Response:
        data = await _cutout(ra, dec, size, id)
        return Response(data, media_type="application/fits", headers={"Cache-Control": "public, max-age=86400"})

    @router.get(f"{API_PREFIX}/reference/deep/cutout.png")
    async def reference_cutout_png(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        size: float = Query(60.0, ge=1, le=cadc_ref.MAX_CUTOUT_ARCSEC),
        id: str = Query(..., pattern=r"^ivo://cadc\.nrc\.ca/[A-Za-z0-9_./?+-]+$"),
    ) -> Response:
        data = await _cutout(ra, dec, size, id)
        try:
            png, facts = await asyncio.to_thread(cadc_ref.to_png, data)
        except cadc_ref.CadcError as e:
            raise HTTPException(502, str(e)) from e
        return Response(
            png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400", "X-Image-Facts": str(facts).replace("\n", " ")}
        )

    @router.post(f"{API_PREFIX}/export/ades")
    async def export_ades(req: AdesRequest) -> Any:
        """An MPC ADES (pipe-separated) file for a mover lead's positions, and what is missing before it can be sent. Nothing is sent."""
        obs = [ades.Observation(**o.model_dump(exclude={"position_km"}), position_km=o.position_km) for o in req.observations]
        header = ades.Header(
            submitter=req.submitter,
            institution=req.institution,
            observers=req.observers,
            measurers=req.measurers,
            ast_cat=req.ast_cat,
            mode=req.mode,
            comment=req.comment,
        )
        issues = ades.problems(req.track_id, obs, header)
        text = ades.to_psv(req.track_id, obs, header) if _track_id_ok(req.track_id) else ""
        return {"ready": not issues, "problems": issues, "observatory_code": ades.OBSERVATORY_CODE, "text": text}

    return router


def _track_id_ok(track_id: str) -> bool:
    return bool(ades.TRK_SUB.match(track_id))


def register(app: FastAPI, extras: Extras | None = None) -> Extras:
    """Add the extra routes to `app`; returns the Extras so the caller can close it on shutdown."""
    ex = extras or Extras()
    app.include_router(build_router(ex))
    app.state.extras = ex
    return ex
