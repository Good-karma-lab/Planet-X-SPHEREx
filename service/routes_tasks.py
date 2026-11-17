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
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from service import accounts
from service.accounts import agreement
from service.accounts.score import STAGE_OF_STATUS

REF_ID = re.compile(r"^[A-Za-z0-9_\-:.]{1,80}$")
LEADS = ("unexplained", "unchecked")


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


def pick_quarter(viewed: dict[str, int], taken: set[str]) -> str | None:
    """The least-looked quarter this user has not taken; ties go NE, NW, SE, SW."""
    left = [q for q in QUARTERS if q not in taken]
    return min(left, key=lambda q: (viewed.get(q, 0), QUARTERS.index(q))) if left else None


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

    body = query(f.db, None, None, None, 100000, 0)
    return [
        {
            "finding_id": x["id"],
            "field_key": (x.get("scans") or [None])[0],
            "ra": x["ra"],
            "dec": x["dec"],
            "status": x["status"],
            "kind": x["kind"],
            "stage": STAGE_OF_STATUS.get(x["status"]),
        }
        for x in body["finds"]
    ]


def make_tasks_router(st: Any) -> APIRouter:
    router = APIRouter()

    def findings() -> list[dict[str, Any]]:
        source = getattr(st, "task_findings", None)  # a test may put its own list here
        rows = source() if callable(source) else lead_findings(st)
        if rows is None:
            raise HTTPException(503, "the findings database is not loaded")
        return list(rows)

    def next_cell(acc: accounts.Accounts, user_id: str | None, cell_id: str | None, quarter: str | None = None) -> dict[str, Any] | None:
        """A quarter (10′) of a square. The next quarter of the square this user is looking through, while it has one they have
        not looked at; then the least-looked searched square and its least-looked quarter (ties NE, NW, SE, SW). `cell_id` asks
        for one square ("Take this square"), `quarter` for one of its quarters."""
        taken = quarters_looked(acc, user_id) if user_id else {}
        if cell_id is not None:
            if cell_place(cell_id) is None:
                raise HTTPException(404, "no such square")
            q = quarter or pick_quarter(quarters_viewed(acc, [cell_id]).get(cell_id, {}), taken.get(cell_id, set())) or QUARTERS[0]
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
                    "SELECT cell_id FROM acc_tasks WHERE user_id=? AND kind='cell' AND quarter IS NOT NULL ORDER BY created DESC LIMIT 1",
                    (user_id,),
                )
                .fetchone()
            )
            if last is not None:
                here = str(last["cell_id"])
                nxt = pick_quarter(quarters_viewed(acc, [here]).get(here, {}), taken.get(here, set()))
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
        row, q = best[2], best[3]
        return {**row, "quarter": q, "fov": QUARTER_FOV, **quarter_place(row["cell_id"], q)}

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
        leads.sort(key=lambda r: checked.get(r["finding_id"], 0))  # stable: the page's order among the equally checked
        return leads[0] if leads else None

    def next_judged(acc: accounts.Accounts, kind: str, user_id: str | None, finding_id: str | None) -> dict[str, Any] | None:
        """Does it really move? / What is this red source? / What is this spectral anomaly?"""
        rows = findings()
        if finding_id is not None:
            one = next((r for r in rows if r["finding_id"] == finding_id), None)
            if one is None:
                raise HTTPException(404, "no such finding")
            return one
        seen = acc.db.task_refs(user_id, kind) if user_id else set()
        return agreement.get(st, acc.db).pick(kind, rows, user_id, seen)

    def with_ask(task: dict[str, Any] | None) -> dict[str, Any] | None:
        """The question and the answers of a judged task, for the lab's panel."""
        if task is not None and task["kind"] in agreement.KINDS:
            task["question"] = agreement.QUESTION[task["kind"]]
            task["answers"] = [{"id": a, "label": label} for a, label in agreement.ANSWERS[task["kind"]]]
            tree = agreement.tree_of(task["kind"])
            if tree is not None:  # plain questions, not classes: the lab asks the tree and sends the path of answers
                task["tree"] = tree
        return task

    @router.get("/api/tasks/results")
    def results(ids: str = Query(..., max_length=8000)) -> Any:
        """{results:{find id: {kind, result, label, agree, total, text}}}: what people said about red sources and anomalies."""
        acc = accounts.get(st)
        return {"results": agreement.get(st, acc.db).people_say([i for i in ids.split(",") if i][:500])}

    @router.get("/api/tasks/brown-dwarf-queue")
    def brown_dwarf_queue() -> Any:
        """{candidates:[{finding_id, ra, dec, agree, total, at}]}: red sources people call cold brown dwarfs, for the brown-dwarf search."""
        acc = accounts.get(st)
        return {"candidates": agreement.get(st, acc.db).brown_dwarf_queue()}

    @router.get("/api/tasks/next")
    def next_task(
        request: Request,
        kind: str = Query(..., pattern="^(cell|finding|moves|red|anomaly)$"),
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
            have = acc.db.open_task(uid, kind)
            if have is not None:
                return {"task": with_ask(have)}
        if kind == "cell":
            ref = next_cell(acc, uid, cell_id, quarter)
        elif kind == "finding":
            ref = next_finding(acc, uid, finding_id)
        else:
            ref = next_judged(acc, kind, uid, finding_id)
        if ref is None:
            return {"task": None}
        return {"task": with_ask(acc.db.add_task(uid, kind, ref))}

    return router
