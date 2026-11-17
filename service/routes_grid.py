"""The coverage API of the sky grid (service.grid): what the machine and people have done with each square.

    GET  /api/grid/meta                  {scheme, total, cell_arcmin, counts:{searched, viewed, with_finds}, rate_per_hour}
    GET  /api/grid/coverage[?corners=1]  {cells:[{id, state, finds, viewers[, corners]}]}    every square that is not "new"
    GET  /api/grid/cells?ra=&dec=&radius= {cells:[{id, ra, dec, corners, state, finds, viewers, bar, searched_at}]}
    GET  /api/grid/cell/{id}             the same + finds:[finding ids] + field_key + open:{ra, dec, fov}
    POST /api/grid/cell/{id}/viewed      {quarter, confirm:true, result?} → {viewers, quarter, quarters}   signed-in only (401)

People look through a square a quarter (10′ × 10′) at a time. A view is counted only for a signed-in user (`st.current_user`) and
only when they confirm it themselves; a square is viewed by people when all four quarters are. Opening a field never counts.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from service import grid
from service.grid import GridDB

MAX_RADIUS_DEG = 5.0


class Viewed(BaseModel):
    """A person says they have looked through one quarter of a square: their own click ("I have looked through this square", or a
    look task's closing button), never the opening of a field."""

    quarter: Literal["NE", "NW", "SE", "SW"] | None = None  # none: the whole 20′ square was on show, all four quarters
    confirm: Literal[True]
    result: Literal["nothing", "marks"] | None = None


def make_grid_router(st: Any, db: GridDB | None = None) -> APIRouter:
    """`db`: the grid database; by default the service's own (`st.grid`, opened at start)."""
    router = APIRouter()

    def D() -> GridDB:  # noqa: N802
        g = db or getattr(st, "grid", None)
        if g is None:
            raise HTTPException(503, "the product database is not open")
        return g

    @router.get("/api/grid/meta")
    async def meta() -> Any:
        return {
            "scheme": grid.SCHEME,
            "scheme_params": grid.scheme_params(),
            "total": grid.TOTAL,
            "cell_arcmin": grid.FIELD_FOV_ARCMIN,
            "counts": D().counts(),
            "rate_per_hour": D().rate_per_hour(),
        }

    @router.get("/api/grid/coverage")
    async def coverage(response: Response, corners: bool = Query(False, description="also the four corners of each square")) -> Any:
        response.headers["Cache-Control"] = "no-store"
        return {"cells": D().coverage(corners)}

    @router.get("/api/grid/cells")
    async def cells(ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), radius: float = Query(1.0, gt=0)) -> Any:
        if radius > MAX_RADIUS_DEG:
            raise HTTPException(400, f"radius is at most {MAX_RADIUS_DEG} degrees")
        cs = grid.cells_near(ra, dec, radius)
        ids = [c.id for c in cs]
        rows, views = D().rows(ids), D().viewers(ids)
        return {"cells": [grid.view_of(c, rows.get(c.id), views.get(c.id, 0)) for c in cs]}

    def user_of(request: Request | None) -> dict[str, Any] | None:
        cu = getattr(st, "current_user", None)
        return cu(request) if callable(cu) and request is not None else None

    def one(cid: str, request: Request | None = None) -> dict[str, Any]:
        try:
            ce = grid.by_id(cid)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        row = D().get(cid)
        out = grid.view_of(ce, row, D().viewers([cid]).get(cid, 0))
        out["finds"] = json.loads((row or {}).get("find_ids") or "[]") or finds_in(st, ce)
        out["n_finds"] = int((row or {}).get("finds") or 0)
        out["field_key"] = (row or {}).get("field_key") or field_key_of(st, ce)
        out["open"] = {"ra": round(ce.ra, 5), "dec": round(ce.dec, 5), "fov": grid.QUARTER_FOV_ARCMIN, "quarter": None}
        out["error"] = (row or {}).get("error")
        seen = D().quarters_viewed([cid]).get(cid, dict.fromkeys(grid.QUARTERS, 0))
        u = user_of(request)
        mine = D().quarters_of(cid, f"user:{u['id']}") if u else set()
        out["quarters"] = [
            {
                "id": q,
                "ra": round(qc.ra, 6),
                "dec": round(qc.dec, 6),
                "fov": grid.QUARTER_FOV_ARCMIN,
                "corners": qc.corners(),
                "viewers": seen[q],
                "mine": q in mine,
            }
            for q, qc in ((q, grid.quarter(ce, q)) for q in grid.QUARTERS)
        ]
        # the lab opens a square at its first quarter this person has not looked through, at 10′ (people look a quarter at a time)
        first = next((q for q in out["quarters"] if not q["mine"]), out["quarters"][0])
        out["open"] = {"ra": round(first["ra"], 5), "dec": round(first["dec"], 5), "fov": grid.QUARTER_FOV_ARCMIN, "quarter": first["id"]}
        return out

    @router.get("/api/grid/cell/{cid}")
    async def cell(cid: str, request: Request) -> Any:
        return one(cid, request)

    @router.post("/api/grid/cell/{cid}/viewed")
    async def viewed(cid: str, body: Viewed, request: Request) -> Any:
        """Signed-in people only (a guest's look is not counted): 401 without an account."""
        try:
            grid.parse_id(cid)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        u = user_of(request)
        if not u:
            raise HTTPException(401, "sign in to count a square as looked through")
        viewers = 0
        for quarter in (body.quarter,) if body.quarter else grid.QUARTERS:
            viewers = D().view(cid, f"user:{u['id']}", body.result, quarter)
        seen = D().quarters_viewed([cid]).get(cid, dict.fromkeys(grid.QUARTERS, 0))
        return {"viewers": viewers, "quarter": body.quarter, "quarters": sum(1 for n in seen.values() if n > 0)}

    return router


def field_key_of(st: Any, ce: grid.Cell) -> str | None:
    """The key of the 20′ field at the square's centre (the lab opens it), when the exposure index is loaded."""
    if getattr(st, "index", None) is None:
        return None
    from service.products.store import product_key
    from service.workers import fieldjob

    try:
        params = fieldjob.canonical(ce.ra, ce.dec, grid.FIELD_FOV_ARCMIN)
        return product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
    except Exception:
        return None


FIND_KINDS = ("mover", "browndwarf", "pair", "film", "peak")  # red sources and spectral anomalies are not finds of a square's search


def finds_in(st: Any, ce: grid.Cell) -> list[str]:
    """The ids of the Findings rows that lie in the square (for squares searched before the grid: their finds have no ids kept)."""
    import sqlite3

    store_dir = getattr(getattr(st, "cfg", None), "store_dir", None)
    path = store_dir / "findings.db" if store_dir is not None else None
    if path is None or not path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as c:
            q = (
                f"SELECT id FROM finds WHERE kind IN ({','.join('?' * len(FIND_KINDS))}) AND dec>=? AND dec<? AND ra>=? AND ra<?"
                " ORDER BY id LIMIT 500"
            )
            return [r[0] for r in c.execute(q, (*FIND_KINDS, ce.dec0, ce.dec1, ce.ra0, ce.ra1))]
    except sqlite3.Error:
        return []
