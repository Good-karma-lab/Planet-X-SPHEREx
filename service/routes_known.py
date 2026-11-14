"""Routes of the known small bodies: the catalogue, an ephemeris and the crossings of a body's path with SPHEREx exposures.

`make_known_router(st)` returns a router for the app; the state carries `st.known` (service.known.Known).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from service.known import JplUnavailable, Known, normalize_name

API_PREFIX = "/api/v1"
TARGET = re.compile(r"[^A-Za-z0-9 /()'-]")


def make_known_router(st: Any, prefix: str = API_PREFIX) -> APIRouter:
    r = APIRouter()

    def known() -> Known:
        k: Known | None = getattr(st, "known", None)
        if k is None:
            raise HTTPException(503, "the known-bodies client is not running")
        if k.index is None:  # the index is opened by the app's start-up, after the client is made
            k.index = getattr(st, "index", None)
        return k

    def target_of(raw: str) -> str:
        t = TARGET.sub("", normalize_name(raw))[:40]
        if not t:
            raise HTTPException(400, "missing target")
        return t

    @r.get(f"{prefix}/known")
    async def known_list(
        q: str | None = Query(None, max_length=80),
        kind: str = Query("all", max_length=3),
        page: int = Query(0, ge=0, le=100000),
        per: int = Query(25, ge=10, le=100),
        sort: str = Query("number"),
        cls: str = Query("", alias="class", max_length=40),
    ) -> Any:
        """A page of the JPL small-body catalogue (kind a|c|all, sort number|H|diameter|name, class codes), or one body by name (`q`)."""
        try:
            if q:
                return await known().lookup(normalize_name(q))
            return await known().page(kind, page, per, sort, re.sub(r"[^A-Z,]", "", cls))
        except JplUnavailable as e:
            raise HTTPException(502, str(e)) from e

    @r.get(f"{prefix}/ephem")
    async def ephem(
        target: str = Query(..., max_length=80),
        jd: str = Query(..., max_length=4000),
        observer: str = Query("", max_length=12),
    ) -> Any:
        """Geocentric RA/Dec, V and distance of a small body at the Julian dates (UTC, at most 200), from JPL Horizons;
        `observer=spherex` for the spacecraft."""
        tgt = target_of(target)
        try:
            jds = [float(x) for x in jd.split(",") if x][:200]
        except ValueError:
            raise HTTPException(400, "bad jd") from None
        if not jds or not all(2400000 < x < 2500000 for x in jds):
            raise HTTPException(400, "missing jd" if not jds else "jd out of range")
        try:
            rows = await known().ephem(tgt, jds, "500@-163182" if observer == "spherex" else "500")
        except JplUnavailable as e:
            raise HTTPException(502, str(e)) from e
        return {
            "target": tgt,
            "ephem": rows,
            "source": "JPL Horizons, " + ("seen from the SPHEREx spacecraft" if observer == "spherex" else "geocentric"),
        }

    @r.get(f"{prefix}/known/crossings")
    async def crossings(target: str = Query(..., max_length=80)) -> Any:
        """The visits in which the body's path crosses SPHEREx exposures: [{ra, dec, mjd0, mjd1, n}] (the place at mid visit, the
        exposures of a 12′ field)."""
        try:
            return await known().crossings(target_of(target))
        except JplUnavailable as e:
            raise HTTPException(502, str(e)) from e

    return r
