"""The background sweep: the machine walks the squares of the sky grid (service.grid) one after another and searches each.

It runs as its own process (tools/sweep.py), never inside the API that serves people. It starts a private instance of the service
(uvicorn on a port of its own, no web, no warm-up, few workers) and asks it, square by square, for the 20′ field at the square's
centre, the movers search between passes and the brown-dwarf search, exactly like a visitor would. The result of every square goes to
the product database: `searched` with the number of finds, the ids of the finds (the ids Findings gives them) and the bar the search
used; "nothing above the bar" is `searched` with finds = 0 and the same bar. The result files land in the store, where Findings picks
them up as it does for every search (on production: the main instance's Findings, the sweep's instance runs as a standby).

Memory is capped HARD. A watchdog thread reads the resident memory (RSS) of the whole process tree of the private instance (API +
workers) twice a second. When it goes over the cap the instance is stopped (SIGTERM, then SIGKILL after 10 s), the square is recorded
as `failed` with "memory cap", the event is logged and the sweep stops with exit code 3: it never grows past the cap and never takes
memory from the service that serves people. Each worker also gets a per-process limit (SKY_WORKER_MEM_MB, a share of the cap), and
on Linux the whole instance can be put in a systemd scope with MemoryMax (the kernel enforces it). One square at a time.

The bar ("above the bar"): a mover is a motion measured to at least MOVER_SIGMA sigma that shifts by at least one pixel over the
baseline of the passes (service.movers), a brown-dwarf candidate is what service.browndwarfs grades as one; the bar stored with each
square says those thresholds and what the search measured there (passes, sources per pass, the chance of one false track).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from service import grid
from service.grid import GridDB

STOP_MEMORY = 3


def tree_rss_mb(root_pid: int) -> float:
    """Resident memory of a process and all its descendants, in MB (from `ps`, the same on macOS and Linux)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    kids: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        pid, ppid, kb = (int(x) for x in parts)
        kids.setdefault(ppid, []).append(pid)
        rss[pid] = kb
    total, todo, seen = 0, [root_pid], set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        total += rss.get(p, 0)
        todo.extend(kids.get(p, []))
    return total / 1024.0


def tree_pids(root_pid: int) -> list[int]:
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return [root_pid]
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    res, todo = [], [root_pid]
    while todo:
        p = todo.pop()
        res.append(p)
        todo.extend(kids.get(p, []))
    return res


@dataclass
class Watchdog:
    """Polls the RSS of a process tree; over the cap it kills the tree and says so."""

    pid: int
    cap_mb: float
    period_s: float = 0.5
    peak_mb: float = 0.0
    square_peak_mb: float = 0.0
    tripped: bool = False
    _stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="sweep-watchdog").start()

    def stop(self) -> None:
        self._stop.set()

    def reset_square(self) -> None:
        self.square_peak_mb = 0.0

    def _run(self) -> None:
        while not self._stop.wait(self.period_s):
            mb = tree_rss_mb(self.pid)
            self.peak_mb = max(self.peak_mb, mb)
            self.square_peak_mb = max(self.square_peak_mb, mb)
            if mb > self.cap_mb:
                self.tripped = True
                kill_tree(self.pid)
                return


def kill_tree(pid: int, grace_s: float = 10.0) -> None:
    pids = tree_pids(pid)
    for p in pids:
        with contextlib.suppress(OSError):
            os.kill(p, signal.SIGTERM)
    t0 = time.monotonic()
    while time.monotonic() - t0 < grace_s:
        if not any(_alive(p) for p in pids):
            return
        time.sleep(0.2)
    for p in pids:
        with contextlib.suppress(OSError):
            os.kill(p, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie child counts as gone
        r = os.waitpid(pid, os.WNOHANG)
        return r == (0, 0)
    except ChildProcessError:
        return True


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Instance:
    """A private instance of the service for the sweep."""

    store: Path
    cap_mb: float
    workers: int = 2
    port: int = 0
    standby: bool = False
    systemd_scope: bool = False
    proc: subprocess.Popen[bytes] | None = None
    log_path: Path | None = None

    def start(self, timeout_s: float = 180.0) -> str:
        self.port = self.port or free_port()
        env = {
            **os.environ,
            "SKY_STORE": str(self.store),
            "SKY_SERVE_WEB": "0",
            "SKY_WARM": "0",
            "SKY_WORKERS": str(self.workers),
            # every worker may use its share of the cap (the API process and the catalogue caches keep the rest)
            "SKY_WORKER_MEM_MB": str(max(800, int(self.cap_mb * 0.8 / max(1, self.workers)))),
            "SKY_DEEP_WORKERS": "1",
            "SKY_STANDBY": "1" if self.standby else "0",
        }
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "service.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]
        if self.systemd_scope and shutil.which("systemd-run"):
            cmd = ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={int(self.cap_mb)}M", "-p", "MemorySwapMax=0", *cmd]
        self.log_path = self.store / "sweep-instance.log"
        log = open(self.log_path, "ab")  # noqa: SIM115 - kept open for the child's lifetime
        self.proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        base = f"http://127.0.0.1:{self.port}"
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            if self.proc.poll() is not None:
                raise RuntimeError(f"the sweep's service instance exited at start (code {self.proc.returncode}); see {self.log_path}")
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"{base}/api/v1/health", timeout=2).status_code == 200:
                    return base
            time.sleep(1.0)
        raise RuntimeError("the sweep's service instance did not answer /api/v1/health")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            kill_tree(self.proc.pid)


def ask(
    c: httpx.Client, path: str, params: dict[str, Any], until: set[str], limit_s: float, stop: Callable[[], bool], pause: float = 3.0
) -> dict[str, Any]:
    """Ask a route until its state is in `until` (asking keeps the job alive), the time is out, or `stop()` says so."""
    t0 = time.monotonic()
    body: dict[str, Any] = {}
    while time.monotonic() - t0 < limit_s:
        if stop():
            return {"state": "stopped"}
        try:
            r = c.get(path, params=params, timeout=120)
        except httpx.HTTPError as e:
            if stop():
                return {"state": "stopped"}
            return {"state": "failed", "error": f"{path}: {type(e).__name__}"}
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "10")))
            continue
        if r.status_code >= 400:
            return {"state": "failed", "error": f"{path}: HTTP {r.status_code} {r.text[:200]}"}
        body = r.json()
        st = str(body.get("state") or (body.get("job") or {}).get("state") or "")
        if st in until:
            return body
        if st in ("error", "cancelled"):
            return {"state": "failed", "error": f"{path}: job {st}: {(body.get('job') or {}).get('error', '')}"[:300]}
        time.sleep(pause)
    return {"state": "timeout", "error": f"{path}: no answer in {limit_s:.0f} s"}


def search_square(c: httpx.Client, ce: grid.Cell, limit_s: float, stop: Callable[[], bool]) -> dict[str, Any]:
    """The searches of one square: the 20′ field, the movers between passes, the brown dwarfs. Returns the row for the grid."""
    from service.findings import find_id
    from service.movers import MOVER_SHIFT_ARCSEC, MOVER_SIGMA

    q = {"ra": round(ce.ra, 5), "dec": round(ce.dec, 5), "fov": grid.FIELD_FOV_ARCMIN}
    fld = ask(c, "/api/v1/fields", q, {"done"}, limit_s, stop)
    if fld.get("state") != "done":
        return {"state": "failed", "error": fld.get("error") or f"field {fld.get('state')}"}
    meta = fld.get("meta") or {}
    n_passes = len(meta.get("passes") or [])
    bar: dict[str, Any] = {"kind": "sweep", "mover_sigma": MOVER_SIGMA, "mover_shift_arcsec": MOVER_SHIFT_ARCSEC, "passes": n_passes}
    if n_passes < 2:  # nothing to compare: searched, nothing can be above the bar here
        bar["note"] = "fewer than two passes"
        return {"state": "searched", "finds": 0, "find_ids": [], "bar": bar, "field_key": fld.get("key")}
    mv = ask(c, "/api/v1/movers", q, {"done", "failed"}, limit_s, stop)
    if mv.get("state") != "done":
        return {"state": "failed", "error": mv.get("error") or f"movers {mv.get('state')}", "field_key": fld.get("key")}
    res = mv.get("result") or {}
    bar |= {
        "sources_per_pass": res.get("sources_per_pass"),
        "chance_any": (res.get("chance") or {}).get("any"),
        "passes_used": res.get("passes"),
    }
    ids = [
        find_id("mover", float(t["ra_2025_9"]), float(t["dec_2025_9"]))
        for t in res.get("tracks", [])
        if t.get("ra_2025_9") is not None and t.get("check") not in ("same",)  # a known Gaia star moving as Gaia says is not a find
    ]
    known_movers = sum(1 for t in res.get("tracks", []) if t.get("check") == "same")
    bd = ask(c, "/api/v1/browndwarfs", q, {"done", "failed"}, limit_s, stop)
    bdr = bd.get("result") or {}
    ids += [find_id("browndwarf", float(x["ra"]), float(x["dec"])) for x in bdr.get("candidates", []) if x.get("ra") is not None]
    bar["browndwarfs"] = bd.get("state")
    bar["known_movers"] = known_movers
    return {"state": "searched", "finds": len(ids), "find_ids": ids, "bar": bar, "field_key": fld.get("key")}


@dataclass
class SweepResult:
    squares: int = 0
    searched: int = 0
    failed: int = 0
    finds: int = 0
    seconds: float = 0.0
    peak_rss_mb: float = 0.0
    stopped: str = ""
    per_square: list[dict[str, Any]] = field(default_factory=list)

    def rate_per_hour(self) -> float | None:
        return round(self.searched * 3600.0 / self.seconds, 2) if self.seconds > 0 and self.searched else None


def pending(db: GridDB, order: Iterable[grid.Cell], retry_failed: bool = False) -> Iterable[grid.Cell]:
    """The squares still to search, in order (a searched square is skipped; a failed one only when asked to retry)."""
    for ce in order:
        row = db.get(ce.id)
        if row is None or row["state"] in ("new", "queued", "searching") or (retry_failed and row["state"] == "failed"):
            yield ce


def run(
    db: GridDB,
    store: Path,
    cells: Iterable[grid.Cell],
    max_squares: int,
    cap_mb: float,
    workers: int = 2,
    per_search_s: float = 1200.0,
    hours: float = 1.0,
    standby: bool = False,
    systemd_scope: bool = False,
    base: str | None = None,
    say: Callable[[str], None] = print,
    recycle_every: int = 10,
    recycle_share: float = 0.7,
) -> SweepResult:
    """Sweep up to `max_squares` squares, one at a time, under the memory cap. `base` reuses a running instance (tests); else a private
    one is started on `store` and stopped at the end. The private instance is started afresh (same port) after every `recycle_every`
    squares, and before a square whenever its resident memory is above `recycle_share` of the cap: its caches grow by about 150 MB a
    square, so a long sweep is recycled well before the cap, and the cap stays the hard stop for a single square that is too big."""
    out = SweepResult()
    inst: Instance | None = None
    dog: Watchdog | None = None
    t_start = time.monotonic()
    t_end = t_start + hours * 3600.0
    since_start = 0
    if base is None:
        inst = Instance(store=store, cap_mb=cap_mb, workers=workers, standby=standby, systemd_scope=systemd_scope)
        base = inst.start()
        assert inst.proc is not None
        dog = Watchdog(inst.proc.pid, cap_mb)
        dog.start()

    def stop() -> bool:
        return dog is not None and dog.tripped

    def recycle(why: str) -> None:
        nonlocal dog, since_start
        assert inst is not None and dog is not None
        out.peak_rss_mb = max(out.peak_rss_mb, round(dog.peak_mb, 1))
        dog.stop()
        inst.stop()
        db.log("recycle", reason=why)
        say(f"recycling the sweep's instance ({why})")
        inst.start()
        assert inst.proc is not None
        dog = Watchdog(inst.proc.pid, cap_mb)
        dog.start()
        since_start = 0

    db.log("start", cap_mb=cap_mb, workers=workers, max_squares=max_squares)
    try:
        with httpx.Client(base_url=base, headers={"User-Agent": "sky-sweep/1"}) as c:
            for ce in cells:
                if out.squares >= max_squares or time.monotonic() > t_end:
                    out.stopped = out.stopped or ("max squares" if out.squares >= max_squares else "time")
                    break
                if inst is not None and dog is not None:
                    now_mb = tree_rss_mb(inst.proc.pid) if inst.proc is not None else 0.0
                    if since_start >= recycle_every:
                        recycle(f"{since_start} squares")
                    elif now_mb > recycle_share * cap_mb:
                        recycle(f"{now_mb:.0f} MB before the square")
                out.squares += 1
                since_start += 1
                if dog is not None:
                    dog.reset_square()
                db.put(ce.id, "searching", source="sweep")
                t0 = time.monotonic()
                try:
                    row = search_square(c, ce, per_search_s, stop)
                except Exception as e:
                    row = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
                secs = round(time.monotonic() - t0, 1)
                peak = dog.square_peak_mb if dog is not None else 0.0
                if dog is not None and dog.tripped:
                    row = {"state": "failed", "error": f"memory cap: the sweep's processes went over {cap_mb:.0f} MB"}
                st = row.pop("state")
                err = row.pop("error", None)
                db.put(
                    ce.id,
                    st,
                    source="sweep",
                    seconds=secs,
                    peak_rss_mb=round(peak, 1),
                    error=err,
                    searched_at=grid._iso(time.time()) if st == "searched" else None,
                    **row,
                )
                db.log(st, ce.id, seconds=secs, finds=row.get("finds"), peak_rss_mb=round(peak, 1), error=err)
                if st == "searched":
                    out.searched += 1
                    out.finds += int(row.get("finds") or 0)
                else:
                    out.failed += 1
                out.per_square.append({"id": ce.id, "state": st, "finds": row.get("finds"), "seconds": secs, "peak_rss_mb": round(peak, 1)})
                say(f"{ce.id} ra={ce.ra:.3f} dec={ce.dec:.3f} {st} finds={row.get('finds')} {secs}s peak={peak:.0f}MB {err or ''}")
                if dog is not None and dog.tripped:
                    out.stopped = "memory cap"
                    db.log("stop", reason="memory cap", cap_mb=cap_mb)
                    break
    finally:
        out.seconds = round(time.monotonic() - t_start, 1)
        if dog is not None:
            dog.stop()
            out.peak_rss_mb = max(out.peak_rss_mb, round(dog.peak_mb, 1))
        if inst is not None:
            inst.stop()
        db.log("end", **{k: v for k, v in out.__dict__.items() if k != "per_square"})
    return out


def prepare_store(store: Path, shared: Path | None) -> None:
    """A private store for a local sweep: a copy of the shared store's exposure index (a copy-on-write clone where the file system
    has one, so SQLite never writes beside the shared file); everything the sweep writes (downloaded exposures, fields, results) stays
    in the private store."""
    store.mkdir(parents=True, exist_ok=True)
    if shared is None or not (shared / "index").is_dir() or (store / "index").exists():
        return
    (store / "index").mkdir()
    for f in sorted((shared / "index").iterdir()):
        if f.name.endswith("-shm"):  # the -wal holds rows not yet in the main file: copied
            continue
        dst = store / "index" / f.name
        if subprocess.run(["cp", "-c", str(f), str(dst)], capture_output=True).returncode != 0:
            shutil.copy2(f, dst)


def summary_json(r: SweepResult) -> str:
    return json.dumps({k: v for k, v in r.__dict__.items()} | {"rate_per_hour": r.rate_per_hour()}, indent=1)
