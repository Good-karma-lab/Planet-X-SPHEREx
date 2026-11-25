"""The shadow mode: what the machine's rules would say of a track or a peak, written down beside it and shown to nobody.

For every time-lapse track and Find Planet X peak of findings.db one row in `auto_verdicts` of its own database (store/shadow.db): the
scores of the rules checked offline (ml-training-2026-11-23.md), whether the rule would hide the find, and which inputs were missing.
Nothing reads these rows but the daily report (tools/shadow_report.py): no list, task, status or page changes. The table only grows
(UPDATE and DELETE are refused by triggers); another rule version, or inputs that were not there before, make another row.

  tracks  score_z = z_together; score_share_z = z × n / (frames of the film between the track's first and last visit); score_gain_med =
          the median over its visits of the motion map's peak at the place above the 95th percentile around it. would_hide = not
          (share_z > 6.4 and z >= 5).
  peaks   score_phot = the S/N of the photometry; would_hide = under 6, only for one-pass peaks of detectors 1-3 in the far range (the
          rule was checked there only); elsewhere would_hide is empty and inputs_missing says why.

The motion maps come from the film's picture part (8 bit, cut at S/N 15: inputs_missing says `maps:8bit`); a square searched on a second
node has none. field_null_rank stays empty (`nulls`): the permutations need the candidates a film search keeps nowhere.

Runs only with SKY_SHADOW=1, on the main instance. One small call at a time in the worker pool at the lowest priority (P3), a pause of
DUTY times the call's length after each, none while people are at work; an error is logged and that find left for later; a full disk makes
the loop wait; MAX_ERRORS errors in a row turn it off until the service starts again. The searches never wait for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from service import cpu, node
from service.products.versions import VERSIONS

log = logging.getLogger("sky.shadow")

# ---- the rules (frozen before the fields they were tested on were read; a change is another rule version) --------------------------------
RULES: dict[str, Any] = {
    "track": {"share_z_gt": 6.4, "z_ge": 5.0, "half": 10},
    "peak": {"phot_sn_ge": 6.0, "d0_ge": 200.0, "det": [1, 2, 3]},
    "code": 1,
}
RULE_VERSION = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()[:16]
HALF = int(RULES["track"]["half"])

BATCH = 25  # finds scored in one worker call
DUTY = 4.0  # the pause after a call, in lengths of the call: at most a fifth of one core
EVERY_S = 600.0  # how often a loop with nothing left looks again
START_S = 240.0
PAUSE_S = 30.0
MAX_ERRORS = 20
DISK_FREE_MIN = 10_000_000_000
DISK_USED_MAX = 0.8
DAY_S = 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_verdicts (
  id INTEGER PRIMARY KEY,
  find_id TEXT NOT NULL, kind TEXT NOT NULL, rule_version TEXT NOT NULL, inputs TEXT NOT NULL, made_at REAL NOT NULL,
  score_z REAL, score_share_z REAL, score_gain_med REAL, score_phot REAL, field_null_rank INTEGER, nulls INTEGER,
  would_hide INTEGER, inputs_missing TEXT NOT NULL DEFAULT '', backlog INTEGER NOT NULL DEFAULT 0,
  UNIQUE (find_id, rule_version, inputs)
);
CREATE INDEX IF NOT EXISTS auto_verdicts_find ON auto_verdicts(find_id);
CREATE TABLE IF NOT EXISTS shadow_events (id INTEGER PRIMARY KEY, at REAL NOT NULL, what TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
"""
for _t in ("auto_verdicts", "shadow_events"):
    for _op in ("UPDATE", "DELETE"):
        SCHEMA += (
            f"CREATE TRIGGER IF NOT EXISTS {_t}_no_{_op.lower()} BEFORE {_op} ON {_t} BEGIN SELECT RAISE(ABORT, '{_t} only grows'); END;\n"
        )
COLUMNS = (
    "find_id", "kind", "rule_version", "inputs", "made_at", "score_z", "score_share_z", "score_gain_med", "score_phot", "field_null_rank",
    "nulls", "would_hide", "inputs_missing", "backlog",
)  # fmt: skip


def enabled() -> bool:
    return os.environ.get("SKY_SHADOW", "0") == "1" and not node.is_node()


def db_path(root: Path) -> Path:
    return Path(root) / "shadow.db"


def read_only(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    c.row_factory = sqlite3.Row
    return c


def writer(root: Path) -> sqlite3.Connection:
    c = sqlite3.connect(db_path(root), timeout=10.0, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(SCHEMA)
    return c


# ---- the scores --------------------------------------------------------------------------------------------------------------------------
def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def share(n: int, pts: list[dict[str, Any]], frame_t: Any) -> float | None:
    """The track's visits among the film's frames between its first and last one."""
    ts = [float(frame_t[p["f"]]) for p in pts if 0 <= int(p["f"]) < len(frame_t)]
    if not ts:
        return None
    lo, hi = min(ts), max(ts)
    return float(n) / max(sum(1 for t in frame_t if lo <= t <= hi), 1)


def gain_med(pts: list[dict[str, Any]], z: Any) -> float | None:
    """The median over the visits of [the highest motion-map S/N within one grid point of the place - the 95th percentile of the 21 x 21 cut
    without its central 7 x 7]. `z`: the motion maps (frame, row, column), NaN where masked."""
    from scipy.ndimage import map_coordinates

    out: list[float] = []
    yy, xx = np.mgrid[-HALF : HALF + 1, -HALF : HALF + 1].astype(float)
    for p in pts:
        f = int(p["f"])
        if not 0 <= f < len(z):
            return None
        a = np.asarray(z[f], dtype=np.float32)
        c = map_coordinates(np.where(np.isfinite(a), a, 0.0), [yy + p["y"], xx + p["x"]], order=1, mode="constant", cval=0.0)
        c = c.astype(np.float32)
        ring = c.copy()
        ring[HALF - 3 : HALF + 4, HALF - 3 : HALF + 4] = np.nan
        out.append(float(c[HALF - 1 : HALF + 2, HALF - 1 : HALF + 2].max() - np.nanpercentile(ring, 95)))
    return float(np.median(out)) if out else None


def film_maps(root: Path, film_key: str) -> NDArray[np.float32] | None:
    """The motion maps from the film's picture part (8 bit; None when the film is not on this disk)."""
    from service.products.sxp import decode

    p = root / "products" / "film" / VERSIONS["film"] / film_key[:2] / film_key / "film.sxp"
    if not p.is_file():
        return None
    v = decode(p.read_bytes())[1]["motion"]
    return np.where(v == 0, np.nan, (v.astype(np.float32) - 1.0) / 254.0 * 20.0 - 5.0).astype(np.float32)


def hide_track(z: float | None, share_z: float | None) -> int | None:
    if z is None or share_z is None:
        return None
    r = RULES["track"]
    return int(not (share_z > r["share_z_gt"] and z >= r["z_ge"]))


def _meta(root: Path, kind: str, key: str) -> dict[str, Any] | None:
    try:
        d: dict[str, Any] = json.loads((root / "products" / kind / VERSIONS[kind] / key[:2] / key / "meta.json").read_bytes())
        return d
    except (OSError, ValueError, KeyError):
        return None


def _search_of(root: Path, scans: list[str], track: dict[str, Any], metas: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The film search whose result holds this track (a merged find lists several searches)."""
    for key in scans:
        if key not in metas:
            metas[key] = _meta(root, "filmsearch", key)
        m = metas[key]
        if m and any(t.get("pts") == track.get("pts") for t in m.get("tracks", [])):
            return key, m
    return None


def score_track(root: Path, find: dict[str, Any], metas: dict[str, Any], maps: dict[str, Any]) -> dict[str, Any]:
    """The row of a track."""
    t = find["data"].get("track") or {}
    pts = t.get("pts") or []
    z, missing = _num(t.get("z_together")), []
    got = _search_of(root, find["scans"], t, metas)
    sh = gm = None
    if got is None:
        missing.append("search")
    else:
        m = got[1]
        sh = share(int(t.get("n") or len(pts)), pts, [fr["mjd"] for fr in m.get("frames", [])])
        film = str(m.get("film") or "")
        if film not in maps:
            maps.clear()  # one film at a time in memory
            maps[film] = film_maps(root, film) if film.isalnum() else None
        if maps[film] is None:
            missing.append("maps")
        else:
            missing.append("maps:8bit")
            gm = gain_med(pts, maps[film])
    missing.append("nulls")
    sz = z * sh if z is not None and sh is not None else None
    if sz is None and "search" not in missing:
        missing.append("frames")
    return {"score_z": z, "score_share_z": sz, "score_gain_med": gm, "would_hide": hide_track(z, sz), "inputs_missing": missing}


def score_peak(find: dict[str, Any]) -> dict[str, Any]:
    """The row of a Find Planet X peak. Its search is read from the peak itself (a merged find holds one search's peak whole): the detectors
    of its photometry and where its distances start. The filter of the lists uses the same four conditions."""
    d = find["data"]
    pk = d.get("peak") or {}
    sn, r, missing = _num(pk.get("phot_sn")), RULES["peak"], []
    if sn is None:
        missing.append("phot")
    if d.get("search") != "distant":
        missing.append("rule:sum of passes")
    else:
        if sorted(int(x) for x in pk.get("bydet") or {}) != r["det"]:
            missing.append("rule:detector")
        d0 = _num(((pk.get("knob") or {}).get("d") or [None])[0])
        if d0 is None or d0 < r["d0_ge"]:
            missing.append("rule:near")
    hide = None if missing or sn is None else int(sn < r["phot_sn_ge"])
    return {"score_phot": sn, "would_hide": hide, "inputs_missing": missing}


def _sig(find: dict[str, Any]) -> str:
    """What a verdict was made of: the same find measured anew is another row."""
    d = find["data"]
    src = d.get("track") or d.get("peak") or {}
    return hashlib.sha256(json.dumps([src, sorted(find["scans"])], sort_keys=True).encode()).hexdigest()[:12]


def _row(find: dict[str, Any], inputs: str, got: dict[str, Any], backlog: bool) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(COLUMNS)
    row |= {"find_id": find["id"], "kind": find["kind"], "rule_version": RULE_VERSION, "inputs": inputs, "made_at": time.time()}
    row |= got | {"inputs_missing": ",".join(got["inputs_missing"]), "backlog": int(backlog)}
    return row


def score_batch(root_s: str, limit: int, backlog: bool) -> list[dict[str, Any]]:
    """In a worker: the next finds without a verdict of these rules, at most `limit`. Reads only."""
    root = Path(root_s)
    with contextlib.closing(read_only(db_path(root))) as c:
        have: dict[str, set[str]] = {}
        for r in c.execute("SELECT find_id, inputs FROM auto_verdicts WHERE rule_version=?", (RULE_VERSION,)):
            have.setdefault(r["find_id"], set()).add(r["inputs"])
    out: list[dict[str, Any]] = []
    metas: dict[str, Any] = {}
    maps: dict[str, Any] = {}
    todo: list[tuple[str, dict[str, Any]]] = []
    with contextlib.closing(read_only(root / "findings.db")) as c:
        for r in c.execute("SELECT id, kind, data, scans FROM finds WHERE kind IN ('film', 'peak')"):
            f = {"id": r["id"], "kind": r["kind"], "data": json.loads(r["data"] or "{}"), "scans": json.loads(r["scans"] or "[]")}
            sig = _sig(f)
            if sig not in have.get(f["id"], set()):
                todo.append((sig, f))
    for sig, f in todo:
        if len(out) >= limit:
            break
        got = score_track(root, f, metas, maps) if f["kind"] == "film" else score_peak(f)
        out.append(_row(f, sig, got, backlog))
    return out


# ---- what the rows say (read only) -------------------------------------------------------------------------------------------------------
LATEST = (
    "SELECT v.* FROM auto_verdicts v JOIN (SELECT find_id, MAX(id) AS id FROM auto_verdicts"
    " WHERE rule_version=? GROUP BY find_id) m ON v.id = m.id"
)


def _people(product_db: Path | None) -> dict[str, dict[str, int]]:
    """{find: {real, empty}} people's answers about a find (counts of people; nobody is named or copied)."""
    out: dict[str, dict[str, int]] = {}
    if product_db is None or not product_db.is_file():
        return out
    from service.accounts.score import AGREES

    real = {a for a, stages in AGREES.items() if set(stages) & {"known", "candidate", "confirmed"}}
    votes: dict[str, dict[str, set[str]]] = {}
    with contextlib.closing(read_only(product_db)) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "acc_task_answers" in tables:
            for r in c.execute("SELECT finding_id f, answer a, user_id u FROM acc_task_answers WHERE kind='moves'"):
                w = "real" if r["a"] == "moves" else "empty" if r["a"] in ("still", "artefact") else None
                if w:
                    votes.setdefault(r["f"], {}).setdefault(w, set()).add(r["u"])
        if "acc_answers" in tables:
            for r in c.execute("SELECT finding_id f, answer a, user_id u FROM acc_answers"):
                w = "real" if r["a"] in real else "empty" if AGREES.get(r["a"]) == ("artefact",) else None
                if w:
                    votes.setdefault(r["f"], {}).setdefault(w, set()).add(r["u"])
    for f, w2 in votes.items():
        out[f] = {k: len(w2.get(k, ())) for k in ("real", "empty")}
    return out


def known_peaks(root: Path, peaks: list[dict[str, Any]]) -> set[str]:
    """The peaks the kept answers of JPL name (service/fpx_known.py; nothing is asked)."""
    from service.fpx_known import FpxIdentifier

    no_client: Any = object()  # only files are read
    said = FpxIdentifier(None, root / "fpx_known", client=no_client)
    return {f["id"] for f in peaks if (said.verdict_on(f) or {}).get("state") == "ok"}


def report(root: Path, product_db: Path | None = None, now: float | None = None) -> dict[str, Any]:
    """The shadow mode's numbers: rows, what would be hidden, and where that differs from JPL and from people."""
    now = time.time() if now is None else now
    with contextlib.closing(read_only(db_path(root))) as c:
        rows = [dict(r) for r in c.execute(LATEST, (RULE_VERSION,))]
        total = int(c.execute("SELECT COUNT(*) FROM auto_verdicts").fetchone()[0])
        started = c.execute("SELECT MIN(at) FROM shadow_events WHERE what='on'").fetchone()[0]
        events = [dict(r) for r in c.execute("SELECT at, what, detail FROM shadow_events ORDER BY id DESC LIMIT 20")]
    with contextlib.closing(read_only(root / "findings.db")) as c:
        jpl = {r[0] for r in c.execute("SELECT find_id FROM idents WHERE catalogue='sbident' AND state='ok'")}
        peaks = [
            {
                "id": r["id"],
                "kind": "peak",
                "ra": r["ra"],
                "dec": r["dec"],
                "scans": json.loads(r["scans"] or "[]"),
                "data": json.loads(r["data"]),
            }
            for r in c.execute("SELECT id, ra, dec, scans, data FROM finds WHERE kind='peak'")
        ]
    jpl |= known_peaks(root, peaks)
    people = _people(product_db)
    out: dict[str, Any] = {
        "rule_version": RULE_VERSION, "rows_all": total, "day": round((now - started) / DAY_S, 2) if started else None, "events": events,
    }  # fmt: skip
    for kind in ("film", "peak"):
        ks = [r for r in rows if r["kind"] == kind]
        judged = [r for r in ks if r["would_hide"] is not None]
        new = [r for r in judged if now - r["made_at"] < DAY_S and not r["backlog"]]
        old = [r for r in judged if r["backlog"]]
        hid = [r for r in judged if r["would_hide"]]
        missing: dict[str, int] = {}
        for r in ks:
            for w in filter(None, r["inputs_missing"].split(",")):
                missing[w] = missing.get(w, 0) + 1
        real = {r["find_id"] for r in judged if people.get(r["find_id"], {}).get("real", 0) >= 2}
        empty = [r for r in judged if people.get(r["find_id"], {}).get("empty", 0) >= 1]
        known = {r["find_id"] for r in judged if r["find_id"] in jpl}
        gone = {r["find_id"] for r in hid}
        out[kind] = {
            "finds": len(ks),
            "judged": len(judged),
            "would_hide": len(hid),
            "last_day": {"judged": len(new), "would_hide": sum(r["would_hide"] for r in new)},
            "backlog": {"judged": len(old), "would_hide": sum(r["would_hide"] for r in old)},
            "missing": missing,
            "jpl_known": {"judged": len(known), "would_hide": sorted(known & gone)},
            "people_real": {"judged": len(real), "would_hide": sorted(real & gone)},
            "people_empty": {"judged": len(empty), "kept": sum(1 for r in empty if not r["would_hide"])},
        }
    out["stop"] = stop_rules(out)
    return out


def stop_rules(rep: dict[str, Any]) -> list[str]:
    """Which of the stop rules of the proposal hold now (they are written down; a person decides)."""
    t, out = rep["film"], []
    lost = rep["film"]["jpl_known"]["would_hide"] + rep["peak"]["jpl_known"]["would_hide"]
    if lost:
        out.append(f"{len(lost)} known bodies would be hidden ({'stop' if len(lost) > 1 else 'look'}): {', '.join(lost[:10])}")
    said = t["people_real"]["would_hide"] + rep["peak"]["people_real"]["would_hide"]
    if said:
        out.append(f"would hide what two people called real: {', '.join(said[:10])}")
    d, b = t["last_day"], t["backlog"]
    if d["judged"] >= 30 and b["judged"] >= 30 and b["would_hide"]:
        ratio = (d["would_hide"] / d["judged"]) / (b["would_hide"] / b["judged"])
        if not 0.5 <= ratio <= 2.0:
            out.append(f"the share that would be hidden in a day is {ratio:.2f} of the expected")
    bad = sum(n for w, n in t["missing"].items() if w in ("search", "frames"))
    if t["finds"] and bad > 0.05 * t["finds"]:
        out.append(f"{bad} of {t['finds']} tracks have no inputs for the rule")
    return out


# ---- the loop ----------------------------------------------------------------------------------------------------------------------------
class _Lowest:
    priority = 3  # service.cpu reads it: P3, gives way to everything


class Shadow:
    def __init__(self, st: Any, root: Path) -> None:
        self.st, self.root = st, Path(root)
        self.task: asyncio.Task[None] | None = None
        self.errors = 0
        self.spent = 0.0  # seconds of worker calls since the start

    def event(self, what: str, **detail: Any) -> None:
        with contextlib.closing(writer(self.root)) as c:
            c.execute("INSERT INTO shadow_events(at, what, detail) VALUES (?,?,?)", (time.time(), what, json.dumps(detail)))

    def add(self, rows: list[dict[str, Any]]) -> None:
        marks = ",".join("?" * len(COLUMNS))
        with contextlib.closing(writer(self.root)) as c:
            c.execute("BEGIN")
            c.executemany(
                f"INSERT OR IGNORE INTO auto_verdicts({','.join(COLUMNS)}) VALUES ({marks})", [[r[k] for k in COLUMNS] for r in rows]
            )
            c.execute("COMMIT")

    def busy(self) -> bool:
        sch = getattr(self.st, "scheduler", None)
        return bool(sch is not None and (sch.person_waiting() or sch.person_active()))

    def room(self) -> bool:
        u = shutil.disk_usage(self.root)
        return u.free >= DISK_FREE_MIN and u.used <= DISK_USED_MAX * u.total

    async def work(self, fn: Any, *args: Any) -> Any:
        """One worker call at the lowest priority, then a pause DUTY times its length."""
        while self.busy():
            await asyncio.sleep(PAUSE_S)
        t0 = time.monotonic()
        out = await cpu.run(fn, *args)
        took = time.monotonic() - t0  # the wait for a place counts too: the more others work, the longer the pause
        self.spent += took
        await asyncio.sleep(min(max(1.0, DUTY * took), 300.0))
        return out

    def backlog(self) -> bool:
        """Whether the finds that were there when the mode was first turned on are still being counted."""
        with contextlib.closing(read_only(db_path(self.root))) as c:
            return c.execute("SELECT 1 FROM shadow_events WHERE what='backlog done'").fetchone() is None

    async def step(self) -> bool:
        """One piece of work. -> whether more may wait."""
        backlog = await asyncio.to_thread(self.backlog)
        rows = await self.work(score_batch, str(self.root), BATCH, backlog)
        if rows:
            await asyncio.to_thread(self.add, rows)
        elif backlog:
            await asyncio.to_thread(self.event, "backlog done")
        return bool(rows)

    async def daily(self) -> None:
        from service.storage import product_db_path

        with contextlib.closing(read_only(db_path(self.root))) as c:
            last = c.execute("SELECT MAX(at) FROM shadow_events WHERE what='day'").fetchone()[0]
        if last is not None and time.time() - last < DAY_S:
            return
        rep = await asyncio.to_thread(report, self.root, product_db_path(self.root))
        short = {k: {w: rep[k][w] for w in ("finds", "judged", "would_hide")} for k in ("film", "peak")}
        log.info("shadow: %s, %.0f s of work since the start", json.dumps(short), self.spent)
        for s in rep["stop"]:
            log.warning("shadow stop rule: %s", s)
        await asyncio.to_thread(self.event, "day", **short, stop=rep["stop"], spent_s=round(self.spent))

    async def loop(self) -> None:
        cpu.JOB.set(_Lowest())
        await asyncio.sleep(START_S)
        try:
            await asyncio.to_thread(self.event, "on", rule=RULE_VERSION, rules=RULES)
        except Exception:
            log.exception("shadow: its database cannot be written, off until the next start")
            return
        while True:
            more = False
            try:
                if self.room():
                    more = await self.step()
                if not more:
                    await self.daily()
                self.errors = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self.errors += 1
                log.exception("shadow: a step failed (%d in a row)", self.errors)
                if self.errors >= MAX_ERRORS:
                    log.warning("shadow stop rule: %d errors in a row, off until the next start", self.errors)
                    return
            await asyncio.sleep(1.0 if more else EVERY_S)


def start(st: Any, root: Path) -> Shadow | None:
    """The loop, when it is turned on (SKY_SHADOW=1, not on a second node). Call it on the main instance only. Never raises."""
    if not enabled():
        return None
    s = Shadow(st, root)
    s.task = asyncio.get_running_loop().create_task(s.loop())
    return s


async def stop(s: Shadow | None) -> None:
    if s is not None and s.task is not None:
        s.task.cancel()
        await asyncio.wait({s.task}, timeout=5.0)  # never holds a restart up
