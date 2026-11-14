"""`GET /api/v1/movers`: the search between passes (service.movers) and the pairs that change in place (service.pairs) of one field, in
one answer.

The body is the one the movers route has always returned, plus
    pairs          the finds, strongest first (kind 'pair': both components, flux per pass and detector, ratio, verdict, crops,
    SIMBAD/Gaia per component)
    chance_pairs   {candidates, steady_pairs, tested, passed, rate, expected, expected_upper95, rate_upper95}: what the pair test finds
    by chance in this field
    chance         {mean, trials} of the search between passes (the same numbers as result.chance)
    mjd            the middle of every pass
    pairs_state    running | done | failed (pairs is empty and chance_pairs null until done); pairs_error
The same keys are inside `result` once the search between passes is done.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from service.workers import fieldjob


def make_pairs_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    from service.products.store import product_key

    router = APIRouter()

    @router.get(f"{prefix}/movers")
    async def movers_of(
        ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), fov: float = Query(5, gt=0, le=60)
    ) -> Any:
        """The search between passes of a field (once the field is finished), the Gaia check of its tracks and the pairs that change in
        place."""
        if st.index is None or st.store is None or st.movers is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra, dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if not st.store.is_complete(key):
            return {"state": "waiting", "error": "", "field": key}
        body: dict[str, Any] = st.movers.view(st.movers.get_or_start(key))
        extra: dict[str, Any] = {"pairs": [], "chance_pairs": None, "pairs_state": "unavailable", "pairs_error": ""}
        meta = st.store.read_meta(fieldjob.KIND, key)
        extra["mjd"] = [round(float(p["mjd_mid"]), 4) for p in meta["passes"]] if meta else []
        pairs = getattr(st, "pairs", None)
        if pairs is not None:
            extra.update(pairs.view(pairs.get_or_start(key)))
        result = body.get("result")
        dwarfs = getattr(st, "dwarfs", None)
        if dwarfs is not None and result is not None:
            # moving red sources (brown dwarfs) that the white stacks do not show are tracks too: the search is done when that scan is
            drun = dwarfs.get_or_start(key)
            if drun.state == "running":
                body["state"], body["phase"] = "running", "looking for moving red sources"
            elif drun.state == "done":
                red = dwarfs.red_tracks(drun)
                result["tracks"] = list(result["tracks"]) + red
                result["red_tracks"] = len(red)
        extra["chance"] = None if result is None else {k: result["chance"][k] for k in ("mean", "trials")}
        body.update(extra)
        if result is not None:
            result.update({k: extra[k] for k in ("pairs", "chance_pairs", "chance", "mjd")})
        return body

    return router
