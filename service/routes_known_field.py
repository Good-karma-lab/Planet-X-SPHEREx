"""`GET /api/known`: the known objects of a frame (Gaia DR3, SIMBAD, JPL) with their places at each epoch (service.known_field).

`make_known_field_router(st)` returns a router for the app; the state gets `st.known_field` on first use.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from service.known_field import MAX_EPOCHS, KnownField


def known_field_of(st: Any) -> KnownField:
    kf: KnownField | None = getattr(st, "known_field", None)
    if kf is None:
        catalogue = getattr(st, "catalogue", None)
        kf = KnownField(st.cfg.store_dir, getattr(catalogue, "breakers", None))
        st.known_field = kf
    return kf


def make_known_field_router(st: Any, prefix: str = "/api") -> APIRouter:
    r = APIRouter()

    @r.get(f"{prefix}/known")
    async def known(
        request: Request,
        ra: float = Query(..., ge=0.0, lt=360.0),
        dec: float = Query(..., ge=-90.0, le=90.0),
        size_arcmin: float = Query(..., ge=1.0, le=60.0),
        epochs: str = Query(..., max_length=800),
        compact: int = Query(0, ge=0, le=1),
        match: int = Query(1, ge=0, le=1),
        wait: float = Query(1.0, ge=0.0, le=60.0),
    ) -> Any:
        """Every Gaia DR3 star, SIMBAD object and JPL body of the square frame, with its place at each epoch (a Julian year such as
        2025.93, or an MJD above 3000: the pass date, which JPL bodies need). `answered` says which catalogue answered.
        `compact=1`: objects without a label carry only id, source, positions, mag. `match` (unless `match=0`): the sources of the
        deeper catalogues (Gaia to G 21, CatWISE2020, AllWISE, 2MASS) merged by position, for matching only, never drawn; their states
        in `status.match`. `wait` (seconds, default 1): the answer comes within about that long with whatever is ready; a catalogue still
        being asked is `pending` in `status` and goes on in the background, so the next ask brings more. Sent gzip-compressed."""
        try:
            eps = [float(x) for x in epochs.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "epochs: numbers separated by commas") from None
        if not eps or len(eps) > MAX_EPOCHS or not all(1990.0 <= e <= 2100.0 or 48000.0 <= e <= 90000.0 for e in eps):
            raise HTTPException(400, f"epochs: 1 to {MAX_EPOCHS} Julian years (1990-2100) or MJDs")
        out = await known_field_of(st).objects(round(ra, 5), round(dec, 5), round(size_arcmin, 1), eps, match=bool(match), wait_s=wait)
        if compact:  # objects without a label keep what a thin ring needs and what the passport takes as the source's identity
            keep = ("id", "source", "positions", "mag", "label", "pmra", "pmdec", "parallax", "bp_rp", "ruwe")
            named = ("name", "type", "simbad")  # a SIMBAD object, or a Gaia star SIMBAD names: its name is its identity

            def thin(o: dict[str, Any]) -> dict[str, Any]:
                ks = keep + named if (o["source"] != "gaia" or o.get("simbad")) else keep
                return {k: o[k] for k in ks if k in o}

            out = {**out, "objects": [o if o["label"] else thin(o) for o in out["objects"]]}
            out["match"] = [{k: m[k] for k in ("id", "catalogues", "ids", "positions", "mag") if k in m} for m in out.get("match", [])]
        body = json.dumps(out, separators=(",", ":")).encode()
        headers = {"Vary": "Accept-Encoding"}
        if len(body) > 1000 and "gzip" in request.headers.get("accept-encoding", ""):
            body = await asyncio.to_thread(gzip.compress, body, 5)
            headers["Content-Encoding"] = "gzip"
        return Response(body, media_type="application/json", headers=headers)

    return r


def prewarm(st: Any, ra: float, dec: float, size_arcmin: float, mjds: list[float]) -> None:
    """Starts asking every catalogue of a frame in the background (a field build has just finished), so that the cache is warm before
    anyone presses Known objects. The same rounding as the route, so the lab's ask finds what this kept."""
    if not mjds:
        return
    kf = known_field_of(st)
    eps = [round(m, 4) for m in mjds[:MAX_EPOCHS]]
    task = asyncio.get_running_loop().create_task(kf.objects(round(ra, 5), round(dec, 5), round(size_arcmin, 1), eps, wait_s=0.0))
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
