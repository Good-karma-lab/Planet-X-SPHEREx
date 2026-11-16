# ruff: noqa: E501
"""`GET /api/v1/findings` (also `/api/findings`): the finds of every searched field, read from findings.db (service.findings); nothing is
computed on the request. Product flow (PRODUCT_SPEC §8): every find also carries `stage`, `promise`, `viewers`, `credit` and `clip`.

The field `status` keeps its meaning (what the catalogues said: unexplained / unchecked / known / residual / galaxy / below / halo), because
the pages and exports that exist read it; the status of the flow (new / in_review / submitted / known / artefact / candidate /
confirmed) is the new field `stage`.
"""

from __future__ import annotations

import asyncio
import os
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from service.findings import KINDS, LITE_KEYS, STAGES, VIEW_RESULTS, FlowDB, find_row, query


def flow_of(st: Any) -> FlowDB:
    """The looks people gave the findings (the product database), opened at the first use."""
    f: FlowDB | None = getattr(st, "findings_flow", None)
    if f is None:
        f = st.findings_flow = FlowDB(Path(os.environ.get("SKY_PRODUCT_DB") or (st.cfg.store_dir / "product.sqlite")))
    return f


def card_extras(x: dict[str, Any]) -> dict[str, Any]:
    """What a card needs from the heavy parts of a find: the name a catalogue gave it, the field its search ran on, its group."""
    sb = next((i for i in x.get("idents") or [] if i["catalogue"] == "simbad" and i["state"] == "ok" and i.get("name")), None)
    d = x.get("data") or {}
    return {
        "name": (x.get("identity") or {}).get("name") or (sb["name"] if sb else None),
        "field": d.get("field_params"),
        "group": d.get("group"),
        "passes": len((d.get("track") or (d.get("source") or {}).get("track") or {}).get("mjd") or []) or None,
    }


def make_findings_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    router = APIRouter()

    def need() -> Any:
        f = getattr(st, "findings", None)
        if f is None:
            raise HTTPException(503, "the findings database is not loaded")
        return f

    def enrich(body: dict[str, Any]) -> dict[str, Any]:
        idn = getattr(st, "fpx_known", None)
        body = idn.enrich_findings(body) if idn is not None else body
        clips = clips_or_none()
        people = credits([f["id"] for f in body["finds"]])
        for f in body["finds"]:
            f["clip"] = clips.summary(f, detail=False) if clips is not None else None
            who = people.get(f["id"]) or []
            if who:  # the accounts of the service: who closed a task on it, who marked or submitted it (names only)
                checked = [c["name"] for c in who if c.get("role") == "checked"]
                first = next((c["name"] for c in who if c.get("role") != "checked"), None)
                f["credit"] = {**f["credit"], "people": who, "checked_by": list(dict.fromkeys(checked)), "first_marked_by": first}
                f["viewers"] = max(int(f.get("viewers") or 0), len(checked))
                if f.get("stage") == "new" and checked:
                    f["stage"] = "in_review"
        return body

    def credits(ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        acc = getattr(st, "accounts", None)  # set by the accounts stream when its routes are registered
        if acc is None or not ids:
            return {}
        try:
            return dict(acc.db.credits_of(ids))
        except Exception:  # the credit line is an extra: the list is served without it
            return {}

    def clips_or_none() -> Any:
        try:
            from service.routes_clips import clips_of

            return clips_of(st)
        except HTTPException:
            return None

    async def every_find() -> list[dict[str, Any]]:
        f = need()
        body = await asyncio.to_thread(lambda: query(f.db, None, "all", None, 5000, 0, flow=flow_of(st), sort="promise"))
        return list(body["finds"])

    async def findings(
        kind: str | None = Query(None),
        status: str | None = Query(None, pattern="^(all|unexplained|unchecked|known|residual|galaxy|below|halo)$"),
        place: str | None = Query(None),
        limit: int = Query(300, ge=1, le=2000),
        offset: int = Query(0, ge=0),
        sort: str | None = Query(None, pattern="^(promise)$"),
        stage: str | None = Query(None),
        waiting: bool = Query(False),
        kinds: str | None = Query(None, description="several kinds, comma separated"),
        lite: bool = Query(False, description="only what a card shows (no data, spectrum, catalogue answers)"),
    ) -> Any:
        """{count, total, counts[kind]{all, unexplained, known}, stages{stage: n}, waiting, promising, identification{pending, unavailable, catalogues},
        finds[{id, kind, place, ra, dec, rate, snr, score, chance, status, verdict, idents[{catalogue, state, name, sep, type}], data, scans,
        stage, promise, viewers, credit{found_by, machine, first_marked_by, checked_by, looks}, clip{url, state}}]}."""
        if kind is not None and kind not in KINDS:
            raise HTTPException(422, f"kind is one of {', '.join(KINDS)}")
        if stage is not None and stage not in STAGES:
            raise HTTPException(422, f"stage is one of {', '.join(STAGES)}")
        many = tuple(k for k in (kinds or "").split(",") if k)
        if any(k not in KINDS for k in many):
            raise HTTPException(422, f"kinds are of {', '.join(KINDS)}")
        f = need()
        clips = clips_or_none()
        if clips is not None and os.environ.get("SKY_CLIPS_PRERENDER", "1") == "1":
            clips.prerender(every_find)  # the background queue starts with the first look at the list
        rank = clips.rank if clips is not None else None
        body: dict[str, Any] = await asyncio.to_thread(
            lambda: query(
                f.db,
                kind,
                status,
                place,
                limit,
                offset,
                flow=flow_of(st),
                sort=sort,
                stage=stage,
                waiting=waiting,
                kinds=many or None,
                clip_rank=rank,
            )
        )
        body = enrich(body)
        if lite:
            body["finds"] = [{**{k: x.get(k) for k in LITE_KEYS}, **card_extras(x)} for x in body["finds"]]
        return body

    async def one(find_id: str) -> Any:
        f = need()
        body: dict[str, Any] = await asyncio.to_thread(lambda: query(f.db, None, "all", None, 5000, 0, flow=flow_of(st)))
        hidden: dict[str, Any] = {"finds": []}
        if not any(x["id"] == find_id for x in body["finds"]) and find_row(f.db, find_id) is not None:
            for hs in ("residual", "galaxy", "below", "halo"):  # a find kept out of the lists still has its page
                hidden = await asyncio.to_thread(partial(query, f.db, None, hs, None, 5000, 0, flow=flow_of(st)))
                if any(x["id"] == find_id for x in hidden["finds"]):
                    break
        for x in body["finds"] + hidden["finds"]:
            if x["id"] == find_id:
                return enrich({"finds": [x]})["finds"][0]
        raise HTTPException(404, "no such find")

    async def viewed(find_id: str, body: dict[str, Any] = Body(default={})) -> Any:  # noqa: B008
        """A person looked at the finding (the lab calls it when the task "Check a machine finding" is closed): {result?: moves | artefact |
        known | nothing | marks, user_id?, user_name?} → {viewers}. The name is given by the service's own login, never by the page."""
        f = need()
        if await asyncio.to_thread(find_row, f.db, find_id) is None:
            raise HTTPException(404, "no such find")
        result = body.get("result")
        if result is not None and result not in VIEW_RESULTS:
            raise HTTPException(422, f"result is one of {', '.join(VIEW_RESULTS)}")
        me = getattr(st, "current_user", None)  # the accounts stream's hook, when it is there: the page cannot name the viewer itself
        user = me(body) if callable(me) else None
        n = await asyncio.to_thread(flow_of(st).add_view, find_id, result, (user or {}).get("id"), (user or {}).get("name"))
        return {"viewers": n}

    for pre in dict.fromkeys((prefix, "/api")):
        router.add_api_route(f"{pre}/findings", findings, methods=["GET"], include_in_schema=pre == prefix)
        router.add_api_route(f"{pre}/findings/{{find_id}}", one, methods=["GET"], include_in_schema=pre == prefix)
        router.add_api_route(f"{pre}/findings/{{find_id}}/viewed", viewed, methods=["POST"], include_in_schema=pre == prefix)
    return router
