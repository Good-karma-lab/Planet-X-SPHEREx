"""A watch on the API process's memory, in the log after FIRST_S and then every EVERY_S: what the kernel counts, what glibc holds and
how much of that is free inside it, what Python holds, the sizes of the collections that live as long as the process, and the garbage
collector's pauses. After each line but the first it hands the free pages inside glibc back to the kernel (malloc_trim). It runs in
its own thread. The collector's threshold is raised with it (GC_THRESHOLD)."""

from __future__ import annotations

import ctypes
import gc
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

EVERY_S = 600.0
FIRST_S = 60.0  # the first line, soon after the start: what the rest is compared with

# how many more objects than before the collector lets stand: Python's 700 promotes whatever the threads are in the middle of, and the
# full collections that follow walk every layout kept (3.7 million objects, 1-2 s with all the threads standing, one a minute on prod).
# Measured on E5 beside the service, 30 s of load: 18 full collections at 700, one at 20,000
GC_THRESHOLD = (20_000, 10, 10)

log = logging.getLogger("sky.mem")


class _Mallinfo2(ctypes.Structure):
    _fields_ = [
        (n, ctypes.c_size_t)
        for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")
    ]


def malloc_mb() -> dict[str, int] | None:
    """What glibc's malloc says of all its arenas, MB, and how long it took to say it (CDLL lets go of the GIL for the call); None where
    there is none (the Mac)."""
    try:
        f = ctypes.CDLL(None).mallinfo2
    except (AttributeError, OSError):
        return None
    f.restype = _Mallinfo2
    t = time.perf_counter()
    m = f()
    ms = (time.perf_counter() - t) * 1000.0
    return {
        "arenas": m.arena >> 20,
        "used": m.uordblks >> 20,
        "free": m.fordblks >> 20,
        "mmapped": m.hblkhd >> 20,
        "mmaps": m.hblks,
        "ms": round(ms),
    }


def rss_anon_mb() -> int | None:
    try:
        with open("/proc/self/status") as f:
            return next(int(x.split()[1]) // 1024 for x in f if x.startswith("RssAnon"))
    except (OSError, StopIteration):
        return None


def trim() -> str | None:
    """Hands the free pages of all of glibc's arenas back to the kernel (the GIL is let go for the call, each arena is locked in turn)
    and says how long it took and what RSS it left. None where there is no malloc_trim (the Mac). mallinfo2's `free` does not fall: it
    counts the pages handed back too."""
    try:
        f = ctypes.CDLL(None).malloc_trim
    except (AttributeError, OSError):
        return None
    f.argtypes = [ctypes.c_size_t]
    a, t = rss_anon_mb(), time.perf_counter()
    f(0)
    ms = (time.perf_counter() - t) * 1000.0
    return f"trim: rss_anon_mb {a} -> {rss_anon_mb()} ms {ms:.0f}"


class MemWatch:
    def __init__(self, sizes: dict[str, Callable[[], int]]) -> None:
        self.sizes = sizes
        self.full, self.full_s, self.full_max, self._t0 = 0, 0.0, 0.0, 0.0  # full collections since the last line
        self._stop = threading.Event()

    def _on_gc(self, phase: str, info: dict[str, Any]) -> None:
        if info.get("generation") != 2:
            return
        if phase == "start":
            self._t0 = time.perf_counter()
        else:
            d = time.perf_counter() - self._t0
            self.full, self.full_s, self.full_max = self.full + 1, self.full_s + d, max(self.full_max, d)

    def line(self) -> str:
        got: dict[str, Any] = {}
        for k, f in self.sizes.items():
            try:
                got[k] = f()
            except Exception as e:  # a collection not there yet, or changed while counted
                got[k] = type(e).__name__
        n, s, m = self.full, self.full_s, self.full_max
        self.full, self.full_s, self.full_max = 0, 0.0, 0.0
        return (
            f"memory: rss_anon_mb {rss_anon_mb()} malloc_mb {malloc_mb()} py_blocks {sys.getallocatedblocks()} "
            f"gc2 {n} sum_s {s:.1f} max_s {m:.2f} threads {threading.active_count()} sizes {got}"
        )

    def _run(self) -> None:
        wait = FIRST_S
        while not self._stop.wait(wait):
            log.warning("%s", self.line())
            if wait == EVERY_S and (said := trim()):
                log.warning("%s", said)
            wait = EVERY_S

    def start(self) -> None:
        self._threshold = gc.get_threshold()
        gc.set_threshold(*GC_THRESHOLD)
        gc.callbacks.append(self._on_gc)
        threading.Thread(target=self._run, name="memwatch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._on_gc in gc.callbacks:
            gc.callbacks.remove(self._on_gc)
            gc.set_threshold(*self._threshold)
