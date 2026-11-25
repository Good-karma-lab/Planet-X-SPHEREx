"""A watch on the API process's event loop: when the loop has not turned for STALL_S, the stack of the code holding it is written to the
log (once per stall), so a slow answer of /health (and the standby taking visitors) is traced to the code that caused it."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

from service import memwatch

STALL_S = 1.0
TICK_S = 0.2

log = logging.getLogger("sky.loop")


class LoopWatch:
    def __init__(self) -> None:
        self.last = time.monotonic()
        self.stalls = 0
        self._task: asyncio.Task[None] | None = None
        self._stop = threading.Event()

    async def _tick(self) -> None:
        while True:
            self.last = time.monotonic()
            await asyncio.sleep(TICK_S)

    def _watch(self, loop_thread: int) -> None:
        reported = False
        while not self._stop.wait(TICK_S):
            now, since = time.monotonic(), self.last
            late = now - since
            if late < STALL_S:
                reported = False
                continue
            if reported:
                continue
            reported = True
            self.stalls += 1
            # the loop's own frame shows where it stopped; a stall with no Python frame there is another thread holding the GIL, so
            # every thread that is running Python is written (innermost frames, the watch's own thread left out)
            names = {t.ident: t.name for t in threading.enumerate()}
            parts = []
            for ident, frame in sys._current_frames().items():
                if ident == threading.get_ident():
                    continue
                inner = traceback.extract_stack(frame)[-8:]
                if (
                    ident != loop_thread
                    and inner
                    and inner[-1].name in ("wait", "_worker", "select", "poll", "get", "_wait_for_tstate_lock")
                ):
                    continue  # an idle pool thread
                tag = "event loop" if ident == loop_thread else names.get(ident, str(ident))
                parts.append(f"-- {tag}:\n" + "".join(traceback.format_list(inner)))
            log.warning("event loop held for %.1f s%s; the threads running Python:\n%s", late, self._collected(since), "\n".join(parts))

    @staticmethod
    def _collected(since: float) -> str:
        """The full collection that ended within the stall, if one did: every thread stood for it, and the stacks say nothing of it."""
        end, took = memwatch.last_full
        return f" (a full collection stopped every thread for {took:.1f} s of it)" if end > since else ""

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._tick())
        threading.Thread(target=self._watch, args=(threading.get_ident(),), daemon=True, name="loopwatch").start()

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
