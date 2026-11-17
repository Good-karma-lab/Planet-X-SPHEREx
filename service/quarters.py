"""Square quarters without the cold wait.

A square task opens one quarter (10′) of a searched square in the lab. The sweep built the square's 20′ field, but a quarter is its own
10′ field, built from the exposures when someone first opens it: a minute or more, up to several when the machine is busy. Three
things keep a person off that wait:

  1. a quarter whose field is already built is handed out first (`ready`), the least-looked among them;
  2. while a person looks at one quarter, the next quarter they would get is built in the background (`prebuild`, a person's
     background work, P1);
  3. a small stock of ready quarters is kept for the squares people will be given next (`stock_step`, the warm runner's priority, P2,
     only while no person's job is waiting).

Readiness is the field store's own word (a complete product), cached briefly so that choosing a task stays cheap.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from itertools import islice
from typing import Any

from service.jobs.scheduler import Priority
from service.products.store import product_key
from service.workers import fieldjob

QUARTER_FOV = 10.0
QUARTERS = ("NE", "NW", "SE", "SW")
log = logging.getLogger("sky.quarters")
STOCK_PARALLEL = 2  # stock builds at once (the scheduler's own cap for background work is BG_HEAVY = 2)
STOCK = int(os.environ.get("SKY_QUARTER_STOCK", "40"))  # ready quarters kept ahead (a launch-day crowd); 0 turns the stock off
READY_TTL_S = 600.0  # a ready quarter is checked again after this (the disk keeper may have removed it)
NOT_READY_TTL_S = 30.0


class Quarters:
    def __init__(self, st: Any, place: Callable[[str, str], dict[str, Any]]) -> None:
        self.st = st
        self.place = place  # (square, quarter) -> {"ra", "dec"}
        self._seen: dict[tuple[str, str], tuple[float, bool]] = {}
        # the event loop the scheduler runs on: a build asked from a worker thread (a sync route) is handed to it
        self.loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop, self._loop_thread = loop, threading.get_ident()

    def _params(self, cell_id: str, q: str) -> dict[str, Any] | None:
        p = self.place(cell_id, q)
        if p.get("ra") is None or p.get("dec") is None:
            return None
        return fieldjob.canonical(float(p["ra"]), float(p["dec"]), QUARTER_FOV)

    def _key(self, params: dict[str, Any]) -> str | None:
        if getattr(self.st, "index", None) is None:
            return None
        return product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(self.st.index, params),))

    def ready(self, cell_id: str, q: str) -> bool:
        """The quarter's 10′ field is built (the lab opens it at once)."""
        now = time.time()
        hit = self._seen.get((cell_id, q))
        if hit is not None and now - hit[0] < (READY_TTL_S if hit[1] else NOT_READY_TTL_S):
            return hit[1]
        params = self._params(cell_id, q)
        key = None if params is None else self._key(params)
        ok = bool(key) and getattr(self.st, "store", None) is not None and self.st.store.is_complete(key)
        self._seen[(cell_id, q)] = (now, ok)
        return ok

    def build(self, cell_id: str, q: str, priority: Priority) -> bool:
        """Start building the quarter's field when it is not built yet. True when a build was asked for (or is already running)."""
        submit = getattr(self.st, "submit_field", None)
        if submit is None or self.ready(cell_id, q):
            return False
        params = self._params(cell_id, q)
        if params is None:
            return False

        def go() -> Any:
            try:
                return submit(params, priority)[1]
            except Exception as e:  # a standby, or no exposure index: the quarter is built when someone opens it
                log.warning("quarter %s %s not built ahead: %s: %s", cell_id, q, type(e).__name__, e)
                return None

        if self.loop is not None and threading.get_ident() != self._loop_thread:
            self.loop.call_soon_threadsafe(go)  # the scheduler is not thread-safe: its loop submits the job
            self._seen.pop((cell_id, q), None)
            return True
        return go() is not None

    def prebuild(self, cell_id: str, q: str) -> bool:
        return self.build(cell_id, q, Priority.P1)

    def _jobs_building(self) -> list[Any]:
        sch = getattr(self.st, "scheduler", None)
        if sch is None:
            return []
        return [
            j
            for j in sch.jobs.values()
            if j.kind == fieldjob.KIND
            and j.priority == Priority.P2
            and j.state in ("queued", "running", "paused")
            and float(j.params.get("fov", 0)) == QUARTER_FOV
        ]

    def building(self) -> int:
        """Background builds of quarters queued or running (the stock adds at most STOCK_PARALLEL)."""
        return len(self._jobs_building())

    def stock_step(self, upcoming: Iterable[tuple[str, str]], stock: int = STOCK) -> str | None:
        """One step of the stock: when fewer than `stock` of the quarters people will be given next are ready, build the first one
        that is not (one at a time, never while a person's job waits). Returns what it started, as 'square/quarter'."""
        sch = getattr(self.st, "scheduler", None)
        if stock <= 0 or sch is None or sch.person_waiting() or self.building() >= STOCK_PARALLEL:
            return None
        ahead = list(islice(upcoming, stock * 2))  # the quarters people will be given next, in the order they get them
        ready = sum(1 for c, q in ahead if self.ready(c, q))
        building = {(str(j.params.get("ra")), str(j.params.get("dec"))) for j in self._jobs_building()}
        first_cold = next(
            (
                (c, q)
                for c, q in ahead
                if not self.ready(c, q) and (lambda p: p is None or (str(p["ra"]), str(p["dec"])) not in building)(self._params(c, q))
            ),
            None,
        )
        if ready >= stock or first_cold is None:
            return None
        return f"{first_cold[0]}/{first_cold[1]}" if self.build(*first_cold, Priority.P2) else None
