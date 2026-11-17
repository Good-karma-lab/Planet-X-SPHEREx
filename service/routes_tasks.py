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
                "SELECT c.id, c.field_key, c.finds, (SELECT COUNT(*) FROM grid_views v WHERE v.cell_id=c.id) AS viewers"
                " FROM grid_cells c WHERE c.state='searched' ORDER BY viewers, c.finds DESC, c.searched_at, c.id LIMIT 2000"
            )
            .fetchall()
        )
    except sqlite3.OperationalError:
        return None
    return [{"cell_id": r["id"], "field_key": r["field_key"]} for r in rows]


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

    def next_cell(acc: accounts.Accounts, user_id: str | None, cell_id: str | None) -> dict[str, Any] | None:
        if cell_id is not None:
            place = cell_place(cell_id)
            if place is None:
                raise HTTPException(404, "no such square")
            return {"cell_id": cell_id, "field_key": cell_field_key(acc, cell_id), **place}
        rows = searched_cells(acc)
        if rows is None:
            raise HTTPException(503, "the sky grid is not loaded")
        seen = acc.db.task_refs(user_id, "cell") if user_id else set()
        for r in rows:
            if r["cell_id"] not in seen:
                return {**r, **(cell_place(r["cell_id"]) or {})}
        return None

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
        finding_id: str | None = Query(None, max_length=80),
    ) -> Any:
        acc = accounts.get(st)
        u = acc.current_user(request)
        uid = u["id"] if u else None
        asked = cell_id if kind == "cell" else finding_id
        if asked is not None and not REF_ID.match(asked):
            raise HTTPException(422, "this is not an id")
        if asked is None and uid is not None:
            have = acc.db.open_task(uid, kind)
            if have is not None:
                return {"task": with_ask(have)}
        if kind == "cell":
            ref = next_cell(acc, uid, cell_id)
        elif kind == "finding":
            ref = next_finding(acc, uid, finding_id)
        else:
            ref = next_judged(acc, kind, uid, finding_id)
        if ref is None:
            return {"task": None}
        return {"task": with_ask(acc.db.add_task(uid, kind, ref))}

    return router
