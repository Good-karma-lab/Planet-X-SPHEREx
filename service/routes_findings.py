# ruff: noqa: E501
"""`GET /api/v1/findings` (also `/api/findings`): the finds of every searched field, read from findings.db (service.findings); nothing is
computed on the request. Product flow (PRODUCT_SPEC §8): every find also carries `stage`, `promise`, `viewers`, `credit` and `clip`.

The field `status` keeps its meaning (what the catalogues said: unexplained / unchecked / known / residual / galaxy / below / halo), because
the pages and exports that exist read it; the status of the flow (new / in_review / submitted / known / artefact / candidate /
confirmed) is the new field `stage`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response

from service import jsonsafe
from service.findings import (
    _READ,
    KINDS,
    LITE_KEYS,
    PROMISING_KINDS,
    STAGES,
    VIEW_RESULTS,
    FlowDB,
    Snapshot,
    find_row,
    id_of,
    query,
    snapshot,
)
from service.storage import atomic_write_bytes

log = logging.getLogger("sky.findings")


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
        # a time-lapse track: the date of the film its search ran on (the lab opens straight on that film: built, never a new one)
        **({"film": d["film"]} if x.get("kind") == "film" and isinstance(d.get("film"), (int, float)) else {}),
    }


LIST_TTL_S = 15.0  # the Findings list is the same for every guest: kept this long
LIST_STALE_S = 300.0  # ... and handed out at once this long while a fresh one is made behind it
LIST_KEEP = 64
SNAP_TTL_S = 30.0  # the judged finds every list is made from: judged again behind a request when older than this
WARM_S = 60.0  # the pages everybody opens are made again this often, and kept on disk for the next start
WARM_FILE = "findings-views.json"
PAGE_SIZE = 48  # the Findings page's (web/src/screens/findings/finds.tsx)


def warm_keys() -> list[dict[str, Any]]:
    """The lists the pages ask for as they open: Findings' Promising and its kinds, the kind chips' numbers, Planet X's peaks, and the
    plain list (no filter: what an outside reader asks first; 26 Nov it took 5 s after a start, 33 s at the deploy's own check)."""
    card = {"status": "all", "sort": "promise", "visibility": "seen", "lite": True, "limit": PAGE_SIZE, "faint": "hide"}
    return [
        {},
        {**card, "kinds": ",".join(("mover", "browndwarf", "peak", "film", "pair"))},
        {"status": "all", "visibility": "seen", "lite": True, "limit": 1, "faint": "hide"},
        *({**card, "kind": k} for k in KINDS),
        {"status": "all", "kind": "peak", "limit": 2000, "faint": "hide"},
    ]


def make_findings_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    router = APIRouter()

    aside_cache: dict[str, Any] = {"at": 0.0, "v": {}}

    def people_aside() -> dict[str, dict[str, Any]]:
        """The machine finds people set aside as artefacts (kept out of the leads and the default list), read every 30 s at most."""
        now = time.time()
        if now - aside_cache["at"] > 30.0:
            try:
                from service.accounts import field_people

                aside_cache["v"] = field_people.set_aside(st)
            except Exception:  # no accounts here (a test service, a standby without the database): nobody set anything aside
                aside_cache["v"] = {}
            aside_cache["at"] = now
        v: dict[str, dict[str, Any]] = aside_cache["v"]
        return v

    tracks_cache: dict[str, Any] = {"at": 0.0, "v": {}}

    def people_tracks_said() -> dict[str, dict[str, Any]]:
        """What people said about tracks (missing visits, re-centred and found-again taps), read every 30 s at most."""
        now = time.time()
        if now - tracks_cache["at"] > 30.0:
            try:
                from service import people_tracks

                tracks_cache["v"] = people_tracks.read(st)
            except Exception:  # no accounts here: nobody said anything
                tracks_cache["v"] = {}
            tracks_cache["at"] = now
        v: dict[str, dict[str, Any]] = tracks_cache["v"]
        return v

    raised_cache: dict[str, Any] = {"at": 0.0, "v": {}}

    def people_raised() -> dict[str, str]:
        """The finds people raised up the queue (first in the promise order), read every 30 s at most."""
        now = time.time()
        if now - raised_cache["at"] > 30.0:
            try:
                from service.accounts import people

                raised_cache["v"] = people.raised(st)
            except Exception:  # no accounts here: nothing is raised
                raised_cache["v"] = {}
            raised_cache["at"] = now
        v: dict[str, str] = raised_cache["v"]
        return v

    real_cache: dict[str, Any] = {"at": 0.0, "v": frozenset()}

    def people_real() -> frozenset[str]:
        """The finds a person said are real (never behind the faint filter), read every 30 s at most."""
        now = time.time()
        if now - real_cache["at"] > 30.0:
            try:
                from service.accounts import people

                real_cache["v"] = people.said_real(st)
            except Exception:  # no accounts here: nobody said anything
                real_cache["v"] = frozenset()
            real_cache["at"] = now
        v: frozenset[str] = real_cache["v"]
        return v

    list_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
    list_inflight: dict[tuple[Any, ...], asyncio.Future[Any]] = {}
    list_tasks: set[asyncio.Task[Any]] = set()  # lists made behind a request (kept here: a task nobody holds may be collected)

    def forget(t: asyncio.Task[Any]) -> None:
        list_tasks.discard(t)
        if not t.cancelled():
            t.exception()  # read: the next request tries again

    snap_kept: dict[str, Any] = {"at": 0.0, "snap": None, "task": None}

    def people_said() -> dict[str, Any]:
        return {"aside": people_aside(), "raised": people_raised(), "tracks": people_tracks_said(), "real": people_real()}

    async def make_snap(f: Any) -> Snapshot:
        clips = clips_or_none()
        flow = flow_of(st)  # opened here: two threads that open it at once find it locked
        got: Snapshot = await asyncio.to_thread(
            lambda: snapshot(
                f.db, flow=flow, seen=clips.seen if clips is not None else None, names=names(), **people_said()
            )
        )  # fmt: skip
        snap_kept.update(at=time.monotonic(), snap=got)
        if hasattr(f, "set_leads"):
            f.set_leads(got)
        return got

    async def the_snap(f: Any, max_age: float = SNAP_TTL_S) -> Snapshot:
        """The judged finds: the kept ones at once (judged again behind it when older than `max_age`); the first time they are waited for."""
        task = snap_kept["task"]
        if (snap_kept["snap"] is None or time.monotonic() - snap_kept["at"] > max_age) and (task is None or task.done()):
            task = snap_kept["task"] = asyncio.create_task(make_snap(f))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # read: the next request tries again
        if snap_kept["snap"] is None:
            return await asyncio.shield(task)
        got: Snapshot = snap_kept["snap"]
        return got

    def ckey_of(q: dict[str, Any]) -> tuple[Any, ...]:
        many = tuple(k for k in (q.get("kinds") or "").split(",") if k)
        return (
            q.get("kind"), q.get("status"), None, q.get("limit", 300), 0, q.get("sort"), None, False, many, None, q.get("visibility"), False,
            bool(q.get("lite")), q.get("faint"),
        )  # fmt: skip

    warm_set = frozenset(ckey_of(q) for q in warm_keys())

    def warm_path() -> Path | None:
        cfg = getattr(st, "cfg", None)
        return Path(cfg.store_dir) / WARM_FILE if cfg is not None else None

    async def warm_views() -> None:
        """The lists the pages open with are there before anybody asks: those of the last run from disk at once, then made from the judged
        finds and made again every WARM_S; the writer keeps them on disk for the next start (a start took 5–20 s to the first list)."""
        path = warm_path()
        if path is not None:
            with contextlib.suppress(OSError, ValueError):
                kept = json.loads(await asyncio.to_thread(path.read_text))
                for q in warm_keys():
                    # (kept by a build without the faint filter: the same list with them, for the seconds until it is made again)
                    body = kept.get(json.dumps(q, sort_keys=True)) or kept.get(
                        json.dumps({k: v for k, v in q.items() if k != "faint"}, sort_keys=True)
                    )
                    if body is not None:  # old enough to be made again at the first request, fresh enough to be handed out
                        list_cache.setdefault(
                            ckey_of(q), (time.monotonic() - LIST_TTL_S, Response(body.encode(), media_type="application/json"))
                        )
        while True:
            try:
                f = getattr(st, "findings", None)
                if f is not None:
                    if not await asyncio.to_thread(has_finds, f):
                        return  # a node (A1) has no findings of its own
                    if snap_kept["snap"] is None or time.monotonic() - snap_kept["at"] > WARM_S / 2:
                        await make_snap(f)
                    out = {}
                    for q in warm_keys():
                        made = await made_list(ckey_of(q))
                        out[json.dumps(q, sort_keys=True)] = bytes(made.body).decode()
                    if path is not None and not getattr(f.db, "read_only", True) and getattr(f, "writer", False):
                        await asyncio.to_thread(atomic_write_bytes, path, json.dumps(out).encode())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the first lists of Findings could not be made")
            await asyncio.sleep(WARM_S if getattr(st, "findings", None) is not None else 2.0)

    def has_finds(f: Any) -> bool:
        with _READ:
            return bool(f.db.db.execute("SELECT 1 FROM sqlite_master WHERE name='finds'").fetchone())

    st.warm_findings = warm_views
    # two lists are made at a time: each is Python work that takes its turn with the event loop's, and forty at once (a crowd with forty
    # different filters) left the loop a fortieth
    list_gate = asyncio.Semaphore(2)

    def need() -> Any:
        f = getattr(st, "findings", None)
        if f is None:
            raise HTTPException(503, "the findings database is not loaded")
        return f

    def names() -> Any:
        """What JPL said of the Planet X peaks (None: not looked up here)."""
        idn = getattr(st, "fpx_known", None)
        return idn.verdict_on if idn is not None else None

    def enrich(body: dict[str, Any]) -> dict[str, Any]:
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
        flow = flow_of(st)
        body = await asyncio.to_thread(lambda: query(f.db, None, "all", None, 5000, 0, flow=flow, sort="promise", names=names()))
        body["finds"].sort(key=lambda x: x["kind"] not in PROMISING_KINDS)  # the pictures of the promising kinds are made first
        return list(body["finds"])

    async def findings(
        kind: str | None = Query(None),
        status: str | None = Query(
            None, pattern="^(all|unexplained|unchecked|known|residual|galaxy|below|halo|set_aside|blend|unverified|chain|weak)$"
        ),
        place: str | None = Query(None),
        limit: int = Query(300, ge=1, le=2000),
        offset: int = Query(0, ge=0),
        sort: str | None = Query(None, pattern="^(promise)$"),
        stage: str | None = Query(None),
        waiting: bool = Query(False),
        kinds: str | None = Query(None, description="several kinds, comma separated"),
        ids: str | None = Query(None, max_length=4000, description="only these finds (comma separated ids)"),
        visibility: str | None = Query(
            None, pattern="^(seen|unseen)$", description="seen: only finds whose object is visible in their picture; unseen: the rest"
        ),
        noted: bool = Query(False, description="only the finds that carry a machine note (they look like an artefact)"),
        lite: bool = Query(False, description="only what a card shows (no data, spectrum, catalogue answers)"),
        faint: str | None = Query(
            None, pattern="^(hide)$", description="hide: without the faint Planet X peaks (their number is in `faint`)"
        ),
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
        need()
        clips = clips_or_none()
        if clips is not None and os.environ.get("SKY_CLIPS_PRERENDER", "1") == "1":
            clips.prerender(every_find)  # the background queue starts with the first look at the list
        # every guest asks the same list: the answer of the same question is kept LIST_TTL_S, and requests that arrive while it is
        # being made wait for that one instead of each making their own (a launch-day crowd costs one query, not hundreds)
        ckey = (kind, status, place, limit, offset, sort, stage, waiting, many, ids, visibility, noted, lite, faint)
        hit = list_cache.get(ckey)
        age = time.monotonic() - hit[0] if hit is not None else 0.0
        if hit is not None and age < LIST_TTL_S:
            return hit[1]
        running = list_inflight.get(ckey)
        if hit is not None and age < LIST_STALE_S:  # handed out at once; the fresh one is made behind it
            if running is None:
                t = asyncio.create_task(made_list(ckey))
                list_tasks.add(t)
                t.add_done_callback(forget)
            return hit[1]
        if running is not None:
            return await asyncio.shield(running)
        return await made_list(ckey)

    async def made_list(ckey: tuple[Any, ...]) -> Response:
        """The answer of one question, made and kept; those who ask it meanwhile wait for this one."""
        kind, status, place, limit, offset, sort, stage, waiting, many, ids, visibility, noted, lite, faint = ckey
        f = need()
        clips = clips_or_none()
        rank = clips.rank if clips is not None else None
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        list_inflight[ckey] = fut
        try:
            snap = await the_snap(f) if ids is None else None
            async with list_gate if ids is None else contextlib.nullcontext():  # a few finds by id wait for no list
                body = await make_list(
                    f,
                    kind,
                    status,
                    place,
                    limit,
                    offset,
                    sort,
                    stage,
                    waiting,
                    many,
                    rank,
                    ids,
                    noted,
                    clips,
                    visibility,
                    lite,
                    snap,
                    faint,
                )
            made = Response(await asyncio.to_thread(lambda: jsonsafe.dumps(body).encode()), media_type="application/json")
            fut.set_result(made)
            list_cache.pop(ckey, None)
            list_cache[ckey] = (time.monotonic(), made)  # the newest last
            for k in [k for k in list_cache if k not in warm_set][: max(0, len(list_cache) - LIST_KEEP)]:
                del list_cache[k]
            return made
        except Exception as e:
            fut.set_exception(e)
            fut.exception()  # read: no "never retrieved" warning when nobody else waited
            raise
        finally:
            list_inflight.pop(ckey, None)

    async def make_list(
        f: Any, kind: Any, status: Any, place: Any, limit: int, offset: int, sort: Any, stage: Any, waiting: bool, many: tuple[str, ...],
        rank: Any, ids: str | None, noted: bool, clips: Any, visibility: Any, lite: bool, snap: Snapshot | None = None,
        faint: str | None = None,
    ) -> Any:  # fmt: skip
        said = await asyncio.to_thread(people_said) if snap is None else {}
        body: dict[str, Any] = await asyncio.to_thread(
            lambda: query(
                f.db,
                kind,
                status,
                place,
                limit,
                offset,
                flow=flow_of(st) if snap is None else None,
                sort=sort,
                stage=stage,
                waiting=waiting,
                kinds=many or None,
                clip_rank=rank,
                only=frozenset(i for i in ids.split(",") if i) if ids is not None else None,
                noted=noted,
                seen=clips.seen if clips is not None else None,
                visibility=visibility,
                names=names(),
                snap=snap,
                faint=faint,
                **said,
            )
        )
        if (
            sort == "promise"
            and offset == 0
            and not kind
            and not place
            and not ids
            and status in (None, "all", "leads")
            and hasattr(f, "set_front")
        ):
            f.set_front(tuple(x["id"] for x in body["finds"][:24]))  # identified first, kept for the next start
        body = await asyncio.to_thread(enrich, body)  # it reads the clips' files and the accounts' database
        if lite:
            body["finds"] = [{**{k: x.get(k) for k in LITE_KEYS}, **card_extras(x)} for x in body["finds"]]
        return body

    async def one(find_id: str) -> Any:
        find_id = id_of(find_id)  # its slug too (track-…)
        f = need()
        aside = await asyncio.to_thread(people_aside)
        tracks = await asyncio.to_thread(people_tracks_said)
        # this one find, whatever its status (a hidden one too): the whole list capped at 5000 lost every find past it once there were
        # 6980 (19 Nov 2026), and its page answered 404
        body: dict[str, Any] = await asyncio.to_thread(
            lambda: query(
                f.db, None, "any", None, 1, 0, flow=flow_of(st), aside=aside, tracks=tracks, only=frozenset({find_id}), names=names()
            )
        )
        for x in body["finds"]:
            if x["id"] == find_id:
                return (await asyncio.to_thread(enrich, {"finds": [x]}))["finds"][0]
        raise HTTPException(404, "no such find")

    async def viewed(find_id: str, body: dict[str, Any] = Body(default={})) -> Any:  # noqa: B008
        """A person looked at the finding (the lab calls it when the task "Check a machine finding" is closed): {result?: moves | artefact |
        known | nothing | marks, user_id?, user_name?} → {viewers}. The name is given by the service's own login, never by the page."""
        find_id = id_of(find_id)
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
        if pre == prefix:  # one find by id answers under /api/v1 only: /api/findings/submitted and its like belong to the accounts
            router.add_api_route(f"{pre}/findings/{{find_id}}", one, methods=["GET"])
        router.add_api_route(f"{pre}/findings/{{find_id}}/viewed", viewed, methods=["POST"], include_in_schema=pre == prefix)
    return router
