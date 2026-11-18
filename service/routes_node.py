"""The main service's side of a second node (service/node.py): it lends squares of the grid and takes back what the node made."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from service import grid, node
from service.storage import product_db_path


def make_node_router(st: Any) -> APIRouter:
    r = APIRouter()
    state: dict[str, Any] = {"leases": None, "order": None}

    def leases() -> node.Leases:
        if state["leases"] is None:
            state["leases"] = node.Leases(product_db_path(st.cfg.store_dir))
        lz: node.Leases = state["leases"]
        return lz

    async def signed(request: Request) -> dict[str, Any]:
        body = await request.body()
        if not node.secret():
            raise HTTPException(404)  # no node is set up here: the route does not exist
        if not node.verify(node.secret(), body, request.headers.get("x-sky-node")):
            raise HTTPException(403, "not signed by a node of this service")
        try:
            out: dict[str, Any] = json.loads(body)
        except ValueError as e:
            raise HTTPException(422, "a JSON body") from e
        name = str(out.get("node") or "")
        if not name.isalnum() or len(name) > 24:
            raise HTTPException(422, "node is a short name")
        return out

    @r.post("/api/internal/lease", include_in_schema=False)
    async def lease(request: Request) -> Any:
        """The next square for a node to search (null when every square is searched or held)."""
        body = await signed(request)
        if getattr(st, "grid", None) is None:
            raise HTTPException(503, "the grid is not loaded")
        if state["order"] is None:
            state["order"] = grid.sweep_order()

        def take() -> Any:
            # a fresh walk of the order each time would read 20 000 rows: the walk goes on from where it was, and starts over at its end
            ce = leases().lend(st.grid, state["order"], body["node"])
            if ce is None:
                state["order"] = grid.sweep_order()
                ce = leases().lend(st.grid, state["order"], body["node"])
            return ce

        ce = await asyncio.to_thread(take)
        return {"square": None if ce is None else {"id": ce.id, "ra": ce.ra, "dec": ce.dec}, "lease_s": node.LEASE_S}

    @r.post("/api/internal/result", include_in_schema=False)
    async def result(request: Request) -> Any:
        """What a node made for a square it holds: written here as if this service had built it."""
        body = await signed(request)
        sq = str(body.get("square") or "")
        if body.get("late"):
            # what finished after the square went back (a Find Planet X search): only products and files, from the node that searched it
            row = await asyncio.to_thread(st.grid.get, sq)
            if row is None or row.get("state") != "searched" or row.get("source") != f"node:{body['node']}":
                raise HTTPException(409, "this node did not search that square")
            try:
                got = await asyncio.to_thread(node.apply_late, st.store, st.cfg.store_dir, body)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            return {"ok": True, **got}
        if not await asyncio.to_thread(leases().holds, sq, body["node"]):
            raise HTTPException(409, "this node does not hold that square (its lease ended)")
        try:
            got = await asyncio.to_thread(node.apply_result, st.store, st.cfg.store_dir, st.grid, body)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        await asyncio.to_thread(leases().release, sq)
        f = getattr(st, "findings", None)
        if f is not None and hasattr(f, "kick"):
            f.kick.set()  # the collector reads the new result files now
        return {"ok": True, **got}

    return r
