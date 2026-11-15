"""A watch on the API process's event loop: when the loop has not turned for STALL_S, the stack of the code holding it is written to the
log (once per stall), so a slow answer of /health (and the standby taking visitors) is traced to the code that caused it."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

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
            late = time.monotonic() - self.last
            if late < STALL_S:
                reported = False
                continue
            if reported:
                continue
            reported = True
            self.stalls += 1
            frame = sys._current_frames().get(loop_thread)
            stack = "".join(traceback.format_list(traceback.extract_stack(frame)[-18:])) if frame is not None else "(no frame)"
            log.warning("event loop held for %.1f s; the code holding it:\n%s", late, stack)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._tick())
        threading.Thread(target=self._watch, args=(threading.get_ident(),), daemon=True, name="loopwatch").start()

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
