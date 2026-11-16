"""`GET /api/known`: the known objects of a frame (Gaia DR3, SIMBAD, JPL) with their places at each epoch (service.known_field).

`make_known_field_router(st)` returns a router for the app; the state gets `st.known_field` on first use.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

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
        ra: float = Query(..., ge=0.0, lt=360.0),
        dec: float = Query(..., ge=-90.0, le=90.0),
        size_arcmin: float = Query(..., ge=1.0, le=60.0),
        epochs: str = Query(..., max_length=800),
    ) -> Any:
        """Every Gaia DR3 star, SIMBAD object and JPL body of the square frame, with its place at each epoch (a Julian year such as
        2025.93, or an MJD above 3000: the pass date, which JPL bodies need). `answered` says which catalogue answered."""
        try:
            eps = [float(x) for x in epochs.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "epochs: numbers separated by commas") from None
        if not eps or len(eps) > MAX_EPOCHS or not all(1990.0 <= e <= 2100.0 or 48000.0 <= e <= 90000.0 for e in eps):
            raise HTTPException(400, f"epochs: 1 to {MAX_EPOCHS} Julian years (1990-2100) or MJDs")
        return await known_field_of(st).objects(round(ra, 5), round(dec, 5), round(size_arcmin, 1), eps)

    return r
