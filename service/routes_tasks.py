"""Taking a task: the next square to look through, or the next machine finding to check.

  GET /api/tasks/next?kind=cell|finding|moves|red|anomaly[&cell_id=|&finding_id=]
                                     → {task:{id, kind, cell_id|finding_id, field_key, ra, dec[, question, answers, tree]}|null}
                                       (red and anomaly carry `tree`: steps of plain questions; the done body sends `path`)
  GET /api/tasks/results?ids=a,b     → what people said about red sources and anomalies (service/accounts/agreement.py)
  GET /api/tasks/brown-dwarf-queue   → red sources people call cold brown dwarfs, for the brown-dwarf search

A guest may take a task too (trying needs no account); it is kept for the signed-in user only (GET /api/me/tasks) and closed by
POST /api/me/tasks/{id}/done. A signed-in user gets their own open task of this kind back until it is closed, then the next one:
squares that the machine has searched and the fewest people have looked at come first (those with finds before the empty ones);
findings that are still leads (unexplained, or not yet checked against the catalogues) and the fewest people have checked come first.

Where the choices come from:
  squares   the `grid_cells` / `grid_views` tables of the grid stream in the product database, and service.grid for a square's centre
  findings  the findings database of the service (st.findings)
"""

from __future__ import annotations

import importlib
import json
import math
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from service import accounts
from service.accounts import agreement, people
from service.accounts.score import STAGE_OF_STATUS
from service.quarters import Quarters

REF_ID = re.compile(r"^[A-Za-z0-9_\-:.]{1,80}$")
LEADS = ("unexplained", "unchecked")
WEAK_HIGH = 30  # a red source or anomaly this promising (unexplained, σ ≥ 10) and visible in its picture joins the strong kinds' turn


def finding_group(r: dict[str, Any]) -> int:
    """The turn of a find among the "check a finding" tasks: the moving kinds and dwarfs first (Findings' Promising), then a red source or
    anomaly that is visible in its picture and scores high, then the hundreds of other red sources and anomalies (the sweep adds
    ~10-30 a square): they never push a mover, dwarf, peak or track out of the tasks."""
    from service.findings import PROMISING_KINDS

    if r.get("kind") in PROMISING_KINDS:
        return 0
    if r.get("visible") is True and int(r.get("promise") or 0) >= WEAK_HIGH:
        return 1
    return 2


def _grid() -> Any:
    try:
        return importlib.import_module("service.grid")
    except ImportError:
        return None


def cell_place(cell_id: str) -> dict[str, Any] | None:
    """The centre of a square, from the grid stream's module; None for an id it does not know. Without the module the id is taken
    as it is (no centre)."""
    g = _grid()
    if g is None:
        return {"ra": None, "dec": None}
    try:
        ce = g.by_id(cell_id)
    except (ValueError, KeyError, IndexError):
        return None
    return {"ra": float(ce.ra), "dec": float(ce.dec)}


def searched_cells(acc: accounts.Accounts) -> list[dict[str, Any]] | None:
    """The searched squares, the least viewed first. None while the grid stream has no table in the product database."""
    try:
        rows = (
            acc.db._conn()
            .execute(
                "SELECT c.id, c.field_key, c.finds, (SELECT COUNT(DISTINCT v.viewer) FROM grid_views v WHERE v.cell_id=c.id"
                " AND v.viewer LIKE 'user:%') AS viewers"
                " FROM grid_cells c WHERE c.state='searched' ORDER BY viewers, c.finds DESC, c.searched_at, c.id LIMIT 2000"
            )
            .fetchall()
        )
    except sqlite3.OperationalError:
        return None
    return [{"cell_id": r["id"], "field_key": r["field_key"]} for r in rows]


QUARTERS = ("NE", "NW", "SE", "SW")  # the grid's and the lab's order (north up, east left): ties go NE, NW, SE, SW
QUARTER_FOV = 10.0  # arcmin: people look through a square a quarter at a time


def quarters_viewed(acc: accounts.Accounts, cell_ids: list[str]) -> dict[str, dict[str, int]]:
    """{square: {quarter: signed-in people who looked through it}} from the grid's table; a whole-square view of before the
    quarters counts for all four. Empty while the grid has no table."""
    out: dict[str, dict[str, int]] = {}
    for i in range(0, len(cell_ids), 500):
        part = cell_ids[i : i + 500]
        try:
            rows = (
                acc.db._conn()
                .execute(
                    f"SELECT cell_id, COALESCE(quarter,'') q, COUNT(DISTINCT viewer) n FROM grid_views WHERE viewer LIKE 'user:%'"
                    f" AND cell_id IN ({','.join('?' * len(part))}) GROUP BY cell_id, q",
                    part,
                )
                .fetchall()
            )
        except sqlite3.OperationalError:  # the grid's table, before it had quarters, or not at all
            try:
                rows = (
                    acc.db._conn()
                    .execute(
                        f"SELECT cell_id, '' q, COUNT(DISTINCT viewer) n FROM grid_views WHERE viewer LIKE 'user:%'"
                        f" AND cell_id IN ({','.join('?' * len(part))}) GROUP BY cell_id",
                        part,
                    )
                    .fetchall()
                )
            except sqlite3.OperationalError:
                return {}
        for r in rows:
            d = out.setdefault(r["cell_id"], dict.fromkeys(QUARTERS, 0))
            for q in QUARTERS if r["q"] == "" else (r["q"],):
                if q in d:
                    d[q] += int(r["n"])
    return out


def quarter_place(cell_id: str, q: str) -> dict[str, Any]:
    """The centre of a quarter of a square (the grid's own geometry), or the square's centre without the grid module."""
    g = _grid()
    if g is None or not hasattr(g, "quarter"):
        return cell_place(cell_id) or {"ra": None, "dec": None}
    qc = g.quarter(g.by_id(cell_id), q)
    return {"ra": float(qc.ra), "dec": float(qc.dec)}


def pick_quarter(viewed: dict[str, int], taken: set[str], ready: Any = None) -> str | None:
    """The least-looked quarter this user has not taken; ties go NE, NW, SE, SW. With `ready` (quarter -> its field is built), a
    built quarter goes before one that would make the person wait."""
    left = [q for q in QUARTERS if q not in taken]
    if not left:
        return None
    if ready is not None:
        warm = [q for q in left if ready(q)]
        if warm:
            left = warm
    return min(left, key=lambda q: (viewed.get(q, 0), QUARTERS.index(q)))


def open_quarters(viewed: dict[str, int], taken: set[str]) -> list[str]:
    """A square's quarters this user has not taken, in the order they are given (least looked, then NE, NW, SE, SW)."""
    return sorted((q for q in QUARTERS if q not in taken), key=lambda q: (viewed.get(q, 0), QUARTERS.index(q)))


READY_SCAN = 40  # quarters looked at for a built one before a cold one is given
SPREAD = 8  # people are given a random one of the first SPREAD quarters (built ones first), so not everyone gets the same square
PROMISING_TTL_S = 60.0
# a square task this old is not handed back, nor its square continued: the person moved on (it was given again 11 h later)
OPEN_TASK_KEEP_S = 2 * 3600.0  # the promising quarters are worked out again at most this often
_promising: dict[str, Any] = {"at": 0.0, "rows": [], "st": None}  # kept for one service state (a test's app never sees another's)


def quarter_of(ce: Any, ra: float, dec: float) -> str:
    """The quarter of a square a sky position falls in (the grid's geometry: north half in Dec, east half in RA)."""
    dm, rm = 0.5 * (ce.dec0 + ce.dec1), 0.5 * (ce.ra0 + ce.ra1)
    r = ra % 360.0
    if r < ce.ra0 - 180.0:
        r += 360.0
    return ("N" if dec >= dm else "S") + ("E" if r >= rm else "W")


def promising_quarters(st: Any) -> list[dict[str, Any]]:
    """The quarters of the squares where the machine's finds are most promising: for each quarter holding a lead of the moving kinds
    (movers, brown dwarfs, Planet X peaks, time-lapse tracks, pairs: Findings' Promising), the best of them; the most promising first.
    [{cell_id, quarter, promise, finding_id, kind}]. Worked out at most every PROMISING_TTL_S (a database read: call it in a thread)."""
    if _promising.get("st") is st and time.time() - float(_promising["at"]) < PROMISING_TTL_S:
        return list(_promising["rows"])
    g = _grid()
    rows: list[dict[str, Any]] = []
    try:
        from service.findings import PROMISING_KINDS

        finds = lead_findings(st) or []
    except Exception:
        finds = []
    if g is not None:
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for f in finds:
            if f.get("kind") not in PROMISING_KINDS or f.get("status") not in LEADS:
                continue
            ce = g.cell_at(float(f["ra"]), float(f["dec"]))
            q = quarter_of(ce, float(f["ra"]), float(f["dec"]))
            cur = best.get((ce.id, q))
            if cur is None or int(f.get("promise") or 0) > cur["promise"]:
                best[(ce.id, q)] = {"cell_id": ce.id, "quarter": q, "promise": int(f.get("promise") or 0), "finding_id": f["finding_id"],
                                    "kind": f.get("kind")}  # fmt: skip
        rows = sorted(best.values(), key=lambda r: (-r["promise"], r["cell_id"], QUARTERS.index(r["quarter"])))
    _promising.update(at=time.time(), rows=rows, st=st)
    return list(rows)


RNG = random.Random()


_FAMOUS: set[str] | None = None


def famous_cells() -> set[str]:
    """The squares the five test targets stand in at 2025.9 (Barnard's Star, ε Indi B, WISE 0855, RW Aur, R Dra): not a newcomer's
    first square."""
    global _FAMOUS
    if _FAMOUS is None:
        g = _grid()
        out: set[str] = set()
        try:
            data = json.loads((Path(__file__).resolve().parent.parent / "data" / "targets.json").read_text(encoding="utf-8"))
            for t in data["targets"]:
                dt = 2025.9 - float(t.get("epoch", 2000.0))
                cosd = max(math.cos(math.radians(float(t["dec_deg"]))), 1e-6)
                ra = float(t["ra_deg"]) + float(t.get("pmra_mas_yr") or 0) * dt / 3.6e6 / cosd
                dec = float(t["dec_deg"]) + float(t.get("pmdec_mas_yr") or 0) * dt / 3.6e6
                if g is not None:
                    out.add(g.cell_at(ra % 360.0, dec).id)
        except (OSError, ValueError, KeyError):
            pass
        _FAMOUS = out
    return _FAMOUS


def quarters_looked(acc: accounts.Accounts, user_id: str) -> dict[str, set[str]]:
    """{square: quarters this user has a task for, or has looked through (the grid's views; a whole-square view of before the
    quarters counts for all four)}."""
    out = acc.db.quarters_taken(user_id)
    try:
        rows = (
            acc.db._conn().execute("SELECT cell_id, COALESCE(quarter,'') q FROM grid_views WHERE viewer=?", (f"user:{user_id}",)).fetchall()
        )
    except sqlite3.OperationalError:
        try:
            rows = acc.db._conn().execute("SELECT cell_id, '' q FROM grid_views WHERE viewer=?", (f"user:{user_id}",)).fetchall()
        except sqlite3.OperationalError:
            rows = []
    for r in rows:
        out.setdefault(r["cell_id"], set()).update(set(QUARTERS) if r["q"] == "" else {r["q"]})
    return out


def cell_field_key(acc: accounts.Accounts, cell_id: str) -> str | None:
    try:
        r = acc.db._conn().execute("SELECT field_key FROM grid_cells WHERE id=?", (cell_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if r is None else r["field_key"]


def lead_findings(st: Any) -> list[dict[str, Any]] | None:
    """The machine findings in the order of the Findings page. None when the findings database is not loaded."""
    f = getattr(st, "findings", None)
    if f is None:
        return None
    from service.findings import query

    try:  # what people agreed: set-aside finds are not handed out, raised ones come first
        from service.accounts import field_people, people

        aside, raised = field_people.set_aside(st), people.raised(st)
    except Exception:
        aside, raised = {}, {}
    clips = getattr(st, "clips", None)
    body = query(f.db, None, None, None, 100000, 0, aside=aside, raised=raised, seen=getattr(clips, "seen", None))
    return [
        {
            "finding_id": x["id"],
            "field_key": (x.get("scans") or [None])[0],
            "ra": x["ra"],
            "dec": x["dec"],
            "status": x["status"],
            "kind": x["kind"],
            "stage": STAGE_OF_STATUS.get(x["status"]),
            "visible": x.get("visible"),
            "promise": x.get("promise") or 0,
            **({"raised": x["people_raised"]} if "people_raised" in x else {}),
        }
        for x in body["finds"]
    ]


def find_rows(st: Any) -> list[dict[str, Any]]:
    """The machine findings in the Findings page's order (a test may put its own list in `st.task_findings`); 503 without them."""
    source = getattr(st, "task_findings", None)
    rows = source() if callable(source) else lead_findings(st)
    if rows is None:
        raise HTTPException(503, "the findings database is not loaded")
    return list(rows)


def find_row(st: Any, finding_id: str) -> dict[str, Any] | None:
    """One machine find as a task needs it, with its data (the visits of a track), or None."""
    row = next((r for r in find_rows(st) if r["finding_id"] == finding_id), None)
    if row is None or "data" in row:
        return row
    f = getattr(st, "findings", None)
    if f is not None:
        from service.findings import find_row as one

        full = one(f.db, finding_id)
        if full is not None:
            row = {**row, "data": full.get("data") or {}}
    return row


TASK_FOVS = (10.0, 20.0, 60.0)  # the field sizes a finding task opens at: the smallest that holds the whole track


def task_fov(row: dict[str, Any] | None) -> float:
    """The field a finding task opens at: 10′, or the smallest of 20′ / 60′ that holds the whole track with a margin; a time-lapse
    at its own field (60′ unless it says)."""
    if not row:
        return TASK_FOVS[0]
    data = row.get("data") or {}
    if str(row.get("finding_id", "")).startswith("film:") or row.get("kind") == "film":
        fp = data.get("field_params") or {}
        return float(fp.get("fov") or 60.0)
    tr = data.get("track") or {}
    pts = [(a, d) for a, d in zip(tr.get("ra") or [], tr.get("dec") or [], strict=False) if a is not None and d is not None]
    if len(pts) < 2:
        return TASK_FOVS[0]
    dec0 = sum(d for _, d in pts) / len(pts)
    cosd = math.cos(math.radians(dec0))
    ras = [((a - pts[0][0] + 540.0) % 360.0 - 180.0) * cosd for a, _ in pts]
    span = max(max(ras) - min(ras), max(d for _, d in pts) - min(d for _, d in pts)) * 60.0  # arcmin
    need = span * 1.25 + 2.0
    return next((f for f in TASK_FOVS if f >= need), TASK_FOVS[-1])


#: the question and the answers of the task kinds that are not a series (spec §5e "More ways people help")
ASK = {
    "find_again": ("Tap it if you see it.", (("seen", "Done"), ("not_seen", "I don't see it"), (people.CANT_TELL, "Not sure"))),
    "recentre": (
        "Tap the centre of the object in each picture where the ring sits off it.",
        (("done", "Done"), (people.CANT_TELL, "Not sure")),
    ),
    "bad_picture": ("Does this picture look broken (a streak, cut off, glare)?",
                    (("broken", "Yes, broken"), ("fine", "No, it looks fine"), (people.CANT_TELL, "Not sure"))),
    "second_look": ("Is this a real object?", people.SECOND_LOOK_ANSWERS),
}  # fmt: skip


def make_tasks_router(st: Any) -> APIRouter:
    router = APIRouter()

    def findings() -> list[dict[str, Any]]:
        return find_rows(st)

    qs = Quarters(st, quarter_place)
    st.quarters = qs

    def upcoming(acc: accounts.Accounts, limit: int = 120) -> list[tuple[str, str]]:
        """The quarters a newcomer would be given next, in order (the stock of ready quarters follows it)."""
        rows = searched_cells(acc) or []
        viewed = quarters_viewed(acc, [r["cell_id"] for r in rows[:500]])
        order = sorted(
            ((viewed.get(r["cell_id"], {}).get(q, 0), i, r["cell_id"], q) for i, r in enumerate(rows[:500]) for q in QUARTERS),
            key=lambda t: (t[0], t[1], QUARTERS.index(t[3])),
        )
        # the promising quarters nobody has looked through first (they are given first: pick_cell), then the stock's order
        cells = {r["cell_id"] for r in rows}
        first = [(p["cell_id"], p["quarter"]) for p in promising_quarters(st)[: 2 * SPREAD] if p["cell_id"] in cells]
        first = [cq for cq in first if not viewed.get(cq[0], {}).get(cq[1], 0)]
        rest = [(c, q) for _, _, c, q in order if (c, q) not in set(first)]
        return (first + rest)[:limit]

    st.upcoming_quarters = upcoming

    def next_cell(acc: accounts.Accounts, user_id: str | None, cell_id: str | None, quarter: str | None = None) -> dict[str, Any] | None:
        """A quarter (10′) of a square, with the one after it built in the background (service/quarters.py)."""
        taken = quarters_looked(acc, user_id) if user_id else {}
        t = pick_cell(acc, user_id, cell_id, quarter, taken)
        if t is not None:
            prebuild_after(acc, t["cell_id"], t["quarter"], taken)
        return t

    def prebuild_after(acc: accounts.Accounts, cell_id: str, quarter: str, taken: dict[str, set[str]]) -> None:
        """While the person looks at this quarter, the next one they would get (the same square's next open quarter) is built."""
        viewed = quarters_viewed(acc, [cell_id]).get(cell_id, {})
        # the first of the quarters they would get after this one that is not built yet (a built one needs nothing)
        cold = next((q for q in open_quarters(viewed, taken.get(cell_id, set()) | {quarter}) if not qs.ready(cell_id, q)), None)
        if cold is not None:
            qs.prebuild(cell_id, cold)

    def pick_cell(
        acc: accounts.Accounts, user_id: str | None, cell_id: str | None, quarter: str | None, taken: dict[str, set[str]]
    ) -> dict[str, Any] | None:
        """The next quarter of the square this user is looking through, while it has one they have not looked at; then the
        least-looked searched square and its least-looked quarter (ties NE, NW, SE, SW), a built one before a cold one. `cell_id`
        asks for one square ("Take this square"), `quarter` for one of its quarters."""
        if cell_id is not None:
            if cell_place(cell_id) is None:
                raise HTTPException(404, "no such square")
            q = (
                quarter
                or pick_quarter(quarters_viewed(acc, [cell_id]).get(cell_id, {}), taken.get(cell_id, set()), lambda x: qs.ready(cell_id, x))
                or QUARTERS[0]
            )
            return {
                "cell_id": cell_id,
                "quarter": q,
                "fov": QUARTER_FOV,
                "field_key": cell_field_key(acc, cell_id),
                **quarter_place(cell_id, q),
            }
        if user_id:  # the same square first: the lab asks for the next task after each closing answer
            last = (
                acc.db._conn()
                .execute(
                    "SELECT cell_id FROM acc_tasks WHERE user_id=? AND kind='cell' AND quarter IS NOT NULL AND created>=?"
                    " ORDER BY created DESC LIMIT 1",
                    (user_id, time.time() - OPEN_TASK_KEEP_S),
                )
                .fetchone()
            )
            if last is not None:
                here = str(last["cell_id"])
                nxt = pick_quarter(quarters_viewed(acc, [here]).get(here, {}), taken.get(here, set()), lambda x: qs.ready(here, x))
                if nxt is not None:
                    return {
                        "cell_id": here,
                        "quarter": nxt,
                        "fov": QUARTER_FOV,
                        "field_key": cell_field_key(acc, here),
                        **quarter_place(here, nxt),
                    }
        rows = searched_cells(acc)
        if rows is None:
            raise HTTPException(503, "the sky grid is not loaded")
        rows = [r for r in rows if len(taken.get(r["cell_id"], set())) < len(QUARTERS)]
        # the squares where the machine's finds are most promising first: a quarter nobody has looked through yet that holds one of
        # the best leads (Findings' Promising), a random one of the first SPREAD (people are spread), never a famous target's square
        # for a newcomer; then the stock's order below
        searched = {r["cell_id"]: r for r in rows}
        pq = [p for p in promising_quarters(st) if p["cell_id"] in searched and p["quarter"] not in taken.get(p["cell_id"], set())]
        if pq:
            seen_q = quarters_viewed(acc, list({p["cell_id"] for p in pq}))
            fresh = newcomer(acc, user_id)
            pq = [p for p in pq if not seen_q.get(p["cell_id"], {}).get(p["quarter"], 0) and not (fresh and p["cell_id"] in famous_cells())]
        # only a built one (M-3: a cold promising quarter took 1 min 45 s): the quarter stock builds these first (upcoming), so they
        # come in as they are made; until then the stock's own built quarters are given
        pq = [p for p in pq[:READY_SCAN] if qs.ready(p["cell_id"], p["quarter"])]
        if pq:
            p = RNG.choice(pq[:SPREAD])
            row = searched[p["cell_id"]]
            why = {"finding_id": p["finding_id"], "kind": p["kind"], "promise": p["promise"]}
            return {**row, "quarter": p["quarter"], "fov": QUARTER_FOV, **quarter_place(p["cell_id"], p["quarter"]), "why": why}
        viewed = quarters_viewed(acc, [r["cell_id"] for r in rows[:500]])
        # the square whose least-looked open quarter has the fewest viewers; the grid's own order among equals
        best: tuple[int, int, dict[str, Any], str] | None = None
        for i, r in enumerate(rows[:500]):
            v = viewed.get(r["cell_id"], {})
            open_q = pick_quarter(v, taken.get(r["cell_id"], set()))
            if open_q is not None and (best is None or (v.get(open_q, 0), i) < best[:2]):
                best = (v.get(open_q, 0), i, r, open_q)
        if best is None:
            return None
        # a built quarter first: the open quarters in the order they would be given, the first one whose field is ready (a few
        # dozen looked at); else the least-looked one, built when the lab opens it
        order = sorted(
            (
                (viewed.get(r["cell_id"], {}).get(q, 0), i, QUARTERS.index(q), r, q)
                for i, r in enumerate(rows[:500])
                for q in open_quarters(viewed.get(r["cell_id"], {}), taken.get(r["cell_id"], set()))
            ),
            key=lambda x: x[:3],
        )
        # a newcomer's square is never one a famous test target dominates ("find something new"); and people are spread over the
        # stock: a random one of the first SPREAD built quarters (else of the first SPREAD open ones), not everyone the same
        if newcomer(acc, user_id):
            order = [o for o in order if o[3]["cell_id"] not in famous_cells()] or order
        ready = [(r, q) for _, _, _, r, q in order[:READY_SCAN] if qs.ready(r["cell_id"], q)]
        pool = ready[:SPREAD] or [(r, q) for _, _, _, r, q in order[:SPREAD]]
        if not pool:
            row, q = best[2], best[3]
            return {**row, "quarter": q, "fov": QUARTER_FOV, **quarter_place(row["cell_id"], q)}
        row, q = RNG.choice(pool)
        return {**row, "quarter": q, "fov": QUARTER_FOV, **quarter_place(row["cell_id"], q)}

    def newcomer(acc: accounts.Accounts, user_id: str | None) -> bool:
        """A guest, or a person who has not had a square task yet."""
        if not user_id:
            return True
        r = acc.db._conn().execute("SELECT 1 FROM acc_tasks WHERE user_id=? AND kind='cell' LIMIT 1", (user_id,)).fetchone()
        return r is None

    def next_finding(acc: accounts.Accounts, user_id: str | None, finding_id: str | None) -> dict[str, Any] | None:
        rows = findings()
        if finding_id is not None:
            one = next((r for r in rows if r["finding_id"] == finding_id), None)
            if one is None:
                raise HTTPException(404, "no such finding")
            return one
        seen = acc.db.task_refs(user_id, "finding") if user_id else set()
        checked = acc.db.check_counts()
        leads = [r for r in rows if r.get("status") in LEADS and r["finding_id"] not in seen]
        # a find people raised first, then the strong kinds before the many red sources and anomalies (finding_group), then the
        # least checked, then the most promising; stable: the page's order among the equal
        leads.sort(key=lambda r: ("raised" not in r, finding_group(r), checked.get(r["finding_id"], 0), -int(r.get("promise") or 0)))
        return leads[0] if leads else None

    def next_judged(acc: accounts.Accounts, kind: str, user_id: str | None, finding_id: str | None) -> dict[str, Any] | None:
        """A question series (spec §5e): moves (tracks, slow movers, Planet X peaks), red, anomaly."""
        rows = findings()
        if finding_id is not None:
            one = next((r for r in rows if r["finding_id"] == finding_id), None)
            if one is None:
                raise HTTPException(404, "no such finding")
            return one
        seen = acc.db.task_refs(user_id, kind) if user_id else set()
        return people.get(st, acc.db).pick(kind, rows, user_id, seen)

    def next_other(acc: accounts.Accounts, kind: str, user_id: str | None, finding_id: str | None) -> dict[str, Any] | None:
        """find_again, recentre, bad_picture, second_look."""
        p = people.get(st, acc.db)
        if kind == "bad_picture":
            return p.pick_exposure(user_id)
        if kind == "second_look":
            return p.pick_second_look(user_id)
        rows = findings()
        if finding_id is not None:
            one = next((r for r in rows if r["finding_id"] == finding_id), None)
            if one is None:
                raise HTTPException(404, "no such finding")
            return one
        seen = acc.db.task_refs(user_id, kind) if user_id else set()
        kinds = people.FIND_AGAIN_KINDS if kind == "find_again" else people.RECENTRE_KINDS
        done = (
            {r[0] for r in acc.db._conn().execute("SELECT DISTINCT finding_id FROM acc_taps WHERE kind=? AND user_id=?", (kind, user_id))}
            if user_id
            else set()
        )
        pool = [r for r in rows if r.get("kind") in kinds and r.get("stage") is None and r["finding_id"] not in seen | done]
        return pool[0] if pool else None

    def with_ask(task: dict[str, Any] | None) -> dict[str, Any] | None:
        """What the lab's panel asks: the question series of the find (spec §5e), or the question of the other task kinds. The
        old one-click classes are not offered any more (they asked what a person cannot judge or the machine already knows)."""
        if task is None:
            return None
        kind = task["kind"]
        if kind in ("moves", "red", "anomaly", "finding") and task.get("finding_id"):
            row = find_row(st, task["finding_id"])
            task["fov"] = task_fov(row)  # the whole track in the picture
            fp = ((row or {}).get("data") or {}).get("field_params") or {}
            if str(task["finding_id"]).startswith("film:") and fp.get("ra") is not None and fp.get("dec") is not None:
                # a time-lapse opens on its own field (built, kept): centred on the track it was a 60′ field never made (a cold build)
                task["ra"], task["dec"] = float(fp["ra"]), float(fp["dec"])
            series = people.series_of(str(task["finding_id"]).split(":")[0])
            if series is not None:
                task["series"] = {"id": series, "steps": people.steps_of(series)}
                if series == "track":
                    task["pictures"] = people.picture_list(find_row(st, task["finding_id"]))
        elif kind in ASK:
            q, answers = ASK[kind]
            task["question"] = q
            task["answers"] = [{"id": a, "label": label} for a, label in answers]
            if kind == "bad_picture":
                task["exposure"] = task["finding_id"]
        return task

    @router.get("/api/tasks/results")
    def results(ids: str = Query(..., max_length=8000)) -> Any:
        """{results:{find id: {kind, result, label, agree, total, text}}}: what people said about red sources and anomalies."""
        acc = accounts.get(st)
        wanted = [i for i in ids.split(",") if i][:500]
        out = agreement.get(st, acc.db).people_say(wanted)  # the one-click answers of before
        p = people.get(st, acc.db)
        out.update(p.set_aside_results(wanted))  # artefact marks of two people (spec §5g)
        out.update(p.results(wanted))  # the question series
        return {"results": out}

    @router.get("/api/tasks/brown-dwarf-queue")
    def brown_dwarf_queue() -> Any:
        """{candidates:[{finding_id, ra, dec, agree, total, at}]}: red sources people call cold brown dwarfs, for the brown-dwarf search."""
        acc = accounts.get(st)
        done = getattr(getattr(st, "bd_queue", None), "done", None) or {}
        # each with what the brown-dwarf search made of it, once it has looked (service/people_queue.py)
        return {
            "candidates": [
                {**c, **({"checked": done[c["finding_id"]]} if c.get("finding_id") in done else {})}
                for c in agreement.get(st, acc.db).brown_dwarf_queue()
            ]
        }

    @router.get("/api/tasks/next")
    def next_task(
        request: Request,
        kind: str = Query(..., pattern="^(cell|finding|moves|red|anomaly|find_again|recentre|bad_picture|second_look)$"),
        cell_id: str | None = Query(None, max_length=80),
        quarter: str | None = Query(None, pattern="^(NW|NE|SW|SE)$"),
        finding_id: str | None = Query(None, max_length=80),
    ) -> Any:
        acc = accounts.get(st)
        u = acc.current_user(request)
        uid = u["id"] if u else None
        asked = cell_id if kind == "cell" else finding_id
        if asked is not None and not REF_ID.match(asked):
            raise HTTPException(422, "this is not an id")
        if asked is None and uid is not None and not (kind == "cell" and quarter):
            have = acc.db.open_task(uid, kind, OPEN_TASK_KEEP_S if kind == "cell" else None)
            if have is not None:
                if kind == "cell" and have.get("cell_id") and have.get("quarter"):
                    prebuild_after(acc, have["cell_id"], have["quarter"], quarters_looked(acc, uid))
                return {"task": with_ask(have)}
        if kind == "cell":
            ref = next_cell(acc, uid, cell_id, quarter)
        elif kind == "finding":
            ref = next_finding(acc, uid, finding_id)
        elif kind in people.TASK_KINDS:
            ref = next_other(acc, kind, uid, finding_id)
        else:
            ref = next_judged(acc, kind, uid, finding_id)
        if ref is None:
            return {"task": None}
        task = with_ask(acc.db.add_task(uid, kind, ref))
        if task is not None and ref.get("why"):  # a square given for a promising find in it: which find (the globe card says why)
            task = {**task, "why": ref["why"]}
        return {"task": task}

    return router
