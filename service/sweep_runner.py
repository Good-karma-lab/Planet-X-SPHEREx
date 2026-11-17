"""The background sweep inside the service: the machine walks the squares of the sky grid and searches each, while nobody needs it.

Order: service.grid.sweep_order: the Planet Nine band first (RA 15°-105°, ecliptic latitude -25°…+15°, from its centre outwards),
then the rest of the ecliptic, then the rest of the sky from the ecliptic outwards. Unknown movers and Planet X live near the
ecliptic, so that is where a searched square is worth the most.

One square at a time: its 20′ field is built at the warm runner's priority (P2), then the searches a visitor's field gets (`searches`,
the app's: movers, pairs, brown dwarfs, red sources, anomalies, the asteroid search of the middle pass). The square goes to the grid
as `searched` with its finds and the bar the searches used, so the globe's coverage shows it; its finds reach Findings through the
collector like every search's. Then its 20′ field's pictures are removed (its row and meta stay, so Findings still reads its results;
nobody opens a square at 20′ and a find opens at 10′ on its own), so the sweep costs the disk its result files and the finds' clips.

People first. A new square starts only when
  - no person's job is waiting and nobody asked for new work in the last minute (scheduler.person_active),
  - the quarter stock is not building (it goes first),
  - the disk is below DISK_PAUSE_PCT,
  - the archive answered the last square without new failures (else it waits ARCHIVE_BACKOFF_S);
and a running square yields at every checkpoint of its jobs. What it does is said in /health (`state`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from service import grid
from service.jobs.scheduler import Priority
from service.workers import fieldjob

DISK_PAUSE_PCT = 80.0
ARCHIVE_BACKOFF_S = 600.0
IDLE_S = 30.0  # how often a paused sweep looks again
KEEP_DAYS = 7.0  # a square with a mover or brown-dwarf find keeps its 20′ field this long (people open it), then it is stripped
SQUARE_LIMIT_S = 3600.0  # a square that takes longer is marked failed and left


def disk_used_pct(path: Any) -> float:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return 0.0
    return 100.0 * u.used / u.total if u.total else 0.0


class SweepRunner:
    def __init__(
        self,
        st: Any,
        searches: Callable[[str, grid.Cell], Awaitable[dict[str, Any]]],
        planetx: Callable[[grid.Cell, str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.st = st
        self.searches = searches
        self.planetx = planetx  # Find Planet X on the squares of the Planet Nine band, the same day as their other searches
        self._last_strip = 0.0
        self.enabled = os.environ.get("SKY_SWEEP", "1") != "0"
        self._order: Iterator[grid.Cell] | None = None
        self._backoff_until = 0.0
        self._s3_failures = self._s3_now()
        self.day = datetime.now(UTC).date().isoformat()
        self.status: dict[str, Any] = {"state": "starting", "squares_today": 0, "finds_today": 0, "seconds_today": 0.0}

    # ---- what /health says ----------------------------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        s = dict(self.status)
        if s.get("seconds_today"):
            s["per_hour"] = round(3600.0 * s["squares_today"] / s["seconds_today"], 1)
        return s

    def _say(self, state: str, **kw: Any) -> None:
        today = datetime.now(UTC).date().isoformat()
        if today != self.day:
            self.day = today
            self.status.update(squares_today=0, finds_today=0, seconds_today=0.0)
        self.status.update(state=state, at=round(time.time()), **kw)

    # ---- the gates --------------------------------------------------------------------------------------------------------------
    def _s3_now(self) -> int:
        s3 = getattr(getattr(self.st, "files", None), "s3", None)
        stats = getattr(s3, "stats", None)
        return int(getattr(stats, "failures", 0) or 0) + int(getattr(stats, "retries", 0) or 0)

    def gate(self) -> str | None:
        """Why the sweep must not start a square now, or None."""
        if not self.enabled:
            return "turned off (SKY_SWEEP=0)"
        sch = getattr(self.st, "scheduler", None)
        if sch is None or getattr(self.st, "index", None) is None or getattr(self.st, "submit_field", None) is None:
            return "the service is not ready"
        if sch.person_waiting() or sch.person_active():
            return "people are using the site"
        qs = getattr(self.st, "quarters", None)
        if qs is not None and qs.building():
            return "the quarter stock goes first"
        if time.time() < self._backoff_until:
            return "the archive had errors: waiting"
        if disk_used_pct(self.st.cfg.store_dir) >= DISK_PAUSE_PCT:
            return f"the disk is {DISK_PAUSE_PCT:.0f}% full"
        return None

    # ---- the order --------------------------------------------------------------------------------------------------------------
    def next_square(self) -> grid.Cell | None:
        """The next square not searched or failed yet, in the sweep order (a database read: call it in a thread)."""
        db = getattr(self.st, "grid", None)
        if db is None:
            return None
        if self._order is None:
            self._order = grid.sweep_order()
        for ce in self._order:
            row = db.get(ce.id)
            if row is None or row.get("state") not in ("searched", "failed", "searching"):
                return ce
        return None

    # ---- one square -------------------------------------------------------------------------------------------------------------
    async def square(self, ce: grid.Cell) -> dict[str, Any]:
        db, store = self.st.grid, self.st.store
        t0 = time.time()
        await asyncio.to_thread(db.put, ce.id, "searching", source="sweep")
        self._say("searching", square=ce.id)
        params = fieldjob.canonical(ce.ra, ce.dec, float(grid.FIELD_FOV_ARCMIN))
        key, job = self.st.submit_field(params, Priority.P2)
        while job is not None and job.state not in ("done", "error", "cancelled"):
            if time.time() - t0 > SQUARE_LIMIT_S:
                break
            await asyncio.sleep(5.0)
        if not store.is_complete(key):
            why = (job.error if job is not None else None) or "the field did not finish"
            await asyncio.to_thread(db.put, ce.id, "failed", error=str(why)[:200], source="sweep", seconds=round(time.time() - t0))
            return {"state": "failed", "error": why}
        meta = await asyncio.to_thread(store.read_meta, fieldjob.KIND, key) or {}
        passes = len(meta.get("passes") or [])
        bar: dict[str, Any] = {"kind": "sweep", "passes": passes}
        found: dict[str, Any] = {"find_ids": []}
        if passes < 2:
            bar["note"] = "fewer than two passes: nothing can be seen to move"
        else:
            found = await self.searches(key, ce)
            bar |= found.get("bar") or {}
        ids = list(dict.fromkeys(found.get("find_ids") or []))
        searched_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        await asyncio.to_thread(
            db.put, ce.id, "searched", finds=len(ids), find_ids=ids, bar=bar, field_key=key, searched_at=searched_at, source="sweep",
            seconds=round(time.time() - t0),
        )  # fmt: skip
        if self.planetx is not None and passes >= 2 and grid.priority(ce)[0] == 0:
            self._say("searching", square=ce.id, step="Find Planet X")
            try:
                bar["planetx"] = await self.planetx(ce, key)
            except Exception as e:  # said in the bar; the square's other searches stand
                bar["planetx"] = {"error": f"{type(e).__name__}: {e}"[:160]}
            await asyncio.to_thread(db.put, ce.id, "searched", bar=bar, source="sweep", seconds=round(time.time() - t0))
        secs = round(time.time() - t0)
        if any(i.startswith(("mover:", "browndwarf:")) for i in ids):
            # a square with a mover or a brown-dwarf find keeps its 20′ field for KEEP_DAYS (people open such finds); then stripped
            bar["keep_until"] = round(time.time() + KEEP_DAYS * 86400.0)
            await asyncio.to_thread(db.put, ce.id, "searched", bar=bar, source="sweep", seconds=secs)
            return {"state": "searched", "finds": len(ids), "seconds": secs, "freed": 0, "kept": True}
        # the 20′ field's pictures are not needed once it is searched: its row and meta stay (Findings reads its result files
        # through them), and it is made again if anyone ever asks for it
        freed = await asyncio.to_thread(store.strip, fieldjob.KIND, key)
        return {"state": "searched", "finds": len(ids), "seconds": secs, "freed": freed}

    def strip_expired(self) -> int:
        """Strip the 20′ fields the sweep kept for their finds once KEEP_DAYS have passed (a database read: in a thread)."""
        db, store = self.st.grid, self.st.store
        now = time.time()
        freed = 0
        rows = (
            db._conn()
            .execute("SELECT id, field_key, bar FROM grid_cells WHERE source='sweep' AND state='searched' AND bar LIKE '%keep_until%'")
            .fetchall()
        )
        for r in rows:
            try:
                bar = json.loads(r["bar"] or "{}")
            except ValueError:
                continue
            if float(bar.get("keep_until") or 0) <= now and r["field_key"]:
                freed += store.strip(fieldjob.KIND, r["field_key"])
                bar.pop("keep_until", None)
                bar["stripped_after_days"] = KEEP_DAYS
                db.put(r["id"], "searched", bar=bar, source="sweep")
        return freed

    # ---- the loop ---------------------------------------------------------------------------------------------------------------
    async def loop(self) -> None:
        await asyncio.sleep(120.0)  # the service settles (the warm set, the stock) first
        while True:
            if time.time() - self._last_strip > 3600.0:
                self._last_strip = time.time()
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.strip_expired)
            why = self.gate()
            if why is not None:
                self._say("paused", why=why)
                await asyncio.sleep(IDLE_S)
                continue
            ce = await asyncio.to_thread(self.next_square)
            if ce is None:
                self._say("done", why="every square is searched")
                await asyncio.sleep(3600.0)
                continue
            before = self._s3_now()
            try:
                got = await self.square(ce)
            except Exception as e:  # said, the square marked failed, the next one tried
                got = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:200]}
                await asyncio.to_thread(self.st.grid.put, ce.id, "failed", error=got["error"], source="sweep")
            if self._s3_now() > before:  # the archive failed or retried during this square: give it time
                self._backoff_until = time.time() + ARCHIVE_BACKOFF_S
            if got.get("state") == "searched":
                self.status["squares_today"] += 1
                self.status["finds_today"] += int(got.get("finds") or 0)
                self.status["seconds_today"] += float(got.get("seconds") or 0)
            self._say("idle", last=ce.id, last_result=got)
