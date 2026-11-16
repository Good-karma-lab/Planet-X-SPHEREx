# ruff: noqa: E501
"""The signed-in user's own data. A guest gets 401 on every route here; the rest of the site works without a login.

PATCH  /api/me {name}                         → {user}
GET    /api/me/marks[?field_key=]             → {marks:[{id, field_key, cell_id, ra, dec, pass, label, level, note, created, mjd}]}
POST   /api/me/marks  (a mark; the id may be made by the page, the same id again replaces the mark)   → the mark
DELETE /api/me/marks/{id}  (or ?id=)          → 204
GET    /api/me/tasks                          → {tasks:[{id, kind, cell_id, finding_id, field_key, ra, dec, state, result, mark_ids, created, done_at}]}
POST   /api/me/tasks/{id}/done {result, mark_ids} → {task}
GET    /api/me/history                        → {history:[{id, kind, ref, title, at}]};  POST {kind, ref, title?, key?};  DELETE clears it
GET    /api/me/notifications                  → {notifications:[{id, kind, text, ref, created, read}], unread};  POST …/read {ids?}
GET    /api/me/investigations                 → {investigations:[{id, title, finding_id, mark_ids, notes, frames, checks_job, submitted, checks}]}
POST   /api/me/investigations {title, …}      → the investigation;  PATCH|DELETE /api/me/investigations/{id}
POST   /api/me/investigations/{id}/notes {text} · …/frames {ref, title, note, thumb}   → the investigation
GET    /api/me/findings                       → {findings:[{finding_id, role, status, title, ra, dec, field_key, created}]}
POST   /api/findings/submit {investigation_id, checks_job} → {finding_id}   (409 unless the checks say can_submit)
GET    /api/findings/submitted                → {findings:[…, credit:[{name, role, at}]]}   (public: what people submitted)
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from service import accounts
from service.accounts import score as scoring
from service.accounts import submit as auto
from service.accounts.db import LimitReachedError, iso

ID = r"^[A-Za-z0-9_\-:.]{6,64}$"
REF = re.compile(r"^#/[^\s<>\"']{0,600}$")  # a place inside this app
CELL_RESULTS = ("nothing", "marks")
FINDING_RESULTS = ("moves", "artefact", "known")
MAX_NOTES, MAX_FRAMES, MAX_THUMB = 200, 60, 200_000


class NameIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    hide_from_board: bool | None = None


class MarkIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = Field(None, pattern=ID)
    field_key: str = Field(min_length=1, max_length=80)
    cell_id: str | None = Field(None, max_length=40)
    ra: float = Field(ge=0, le=360)
    dec: float = Field(ge=-90, le=90)
    pass_: int | None = Field(None, alias="pass", ge=0, le=100000)
    label: str = Field("", max_length=80)
    level: Literal["suspicious", "interesting", "artefact"]
    note: str = Field("", max_length=2000)
    created: str | None = Field(None, max_length=40)
    mjd: float | None = Field(None, ge=48000.0, le=90000.0)  # the date of the frame the mark was put on (the lab sends it)


class DoneIn(BaseModel):
    result: str = Field(max_length=20)
    mark_ids: list[str] = Field(default_factory=list, max_length=500)
    # what the lab says about the look (the score: a square counts with at least 60 s and every pass shown)
    seconds: float | None = Field(None, ge=0, le=86400)
    passes_shown: int | None = Field(None, ge=0, le=10000)
    passes_total: int | None = Field(None, ge=0, le=10000)
    local_hour: int | None = Field(None, ge=0, le=23)


class HistoryIn(BaseModel):
    kind: str = Field(pattern=r"^[a-z_]{1,20}$")
    ref: str = Field(max_length=600)
    title: str = Field("", max_length=120)
    key: str | None = Field(None, max_length=120)


class ReadIn(BaseModel):
    ids: list[int] | None = Field(None, max_length=500)


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    at: str | None = Field(None, max_length=40)


class Frame(BaseModel):
    ref: str = Field(max_length=600)
    title: str = Field("", max_length=120)
    note: str = Field("", max_length=2000)
    thumb: str | None = Field(None, max_length=MAX_THUMB)
    at: str | None = Field(None, max_length=40)


class InvestigationIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    finding_id: str | None = Field(None, max_length=80)
    mark_ids: list[str] = Field(default_factory=list, max_length=500)
    notes: list[Note] = Field(default_factory=list, max_length=MAX_NOTES)
    frames: list[Frame] = Field(default_factory=list, max_length=MAX_FRAMES)
    checks_job: str | None = Field(None, max_length=80)


class InvestigationPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    finding_id: str | None = Field(None, max_length=80)
    mark_ids: list[str] | None = Field(None, max_length=500)
    notes: list[Note] | None = Field(None, max_length=MAX_NOTES)
    frames: list[Frame] | None = Field(None, max_length=MAX_FRAMES)
    checks_job: str | None = Field(None, max_length=80)
    ruler: dict[str, Any] | None = None  # {rate, catalogue_rate, object}: a motion measured with the lab's ruler
    old_survey: dict[str, Any] | None = None  # {survey, ra, dec}: the same object found in an old survey


class DeclareIn(BaseModel):
    checks_job: str = Field(min_length=1, max_length=80)
    note: str | None = Field(None, max_length=4000)


class SubmitIn(BaseModel):
    investigation_id: str = Field(max_length=80)
    checks_job: str = Field(min_length=1, max_length=80)


def _now() -> str:
    return iso(time.time()) or ""


def _stamp(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for x in items:
        x["at"] = x.get("at") or _now()
        if "ref" in x and not REF.match(x["ref"]):
            raise HTTPException(422, "ref is a place inside this app (it starts with #/)")
        if x.get("thumb") and not str(x["thumb"]).startswith("data:image/"):
            raise HTTPException(422, "thumb is a small picture as a data URL")
    return items


def make_me_router(st: Any) -> APIRouter:
    router = APIRouter()

    def me(request: Request) -> tuple[accounts.Accounts, dict[str, Any]]:
        acc = accounts.get(st)
        return acc, acc.need_user(request)

    def checks_of(job: str | None) -> dict[str, Any] | None:
        """What the checks stream says about a job: None when the stream is not loaded or the job is unknown."""
        look = getattr(st, "checks_status", None)
        if not job or not callable(look):
            return None
        body = look(job)
        return body if isinstance(body, dict) else None

    def with_checks(inv: dict[str, Any]) -> dict[str, Any]:
        body = checks_of(inv["checks_job"])
        inv["checks"] = None if body is None else {k: body.get(k) for k in ("state", "checks", "can_submit", "needs_comment", "kind")}
        inv["state"] = auto.state_of(inv, body)
        inv["progress"] = auto.progress_of(body) if inv["state"] == "checking" else ""
        return inv

    def fresh(acc: accounts.Accounts, u: dict[str, Any]) -> None:
        """What happened since the user last looked, said once each: a checks job of theirs finished; a square they looked through
        got a machine find. (A finding's new status is said when the verdict is set: AccountsDB.set_status.)"""
        auto.settle_user(st, acc.db, u["id"])  # a declared submit finishes here too, should the checks' own signal be missed
        sc = score_of(acc)
        sc.settle(u["id"])  # answers whose finding got its final stage since
        sc.award(u["id"])
        for inv in acc.db.investigations(u["id"]):
            body = None if inv["submitted"] or inv["declared"] else checks_of(inv["checks_job"])
            if body is not None and body.get("state") == "done":
                ok = body.get("can_submit") is True
                text = f"The checks of “{inv['title']}” have finished: " + ("ready to submit." if ok else "something blocks the submit.")
                acc.db.notify_once(u["id"], f"checks:{inv['checks_job']}", "checks", text, f"#/me?tab=investigations&inv={inv['id']}")
        for cell_id, finds in acc.db.viewed_squares_with_finds(u["id"]):
            text = f"Square {cell_id}, which you looked through, now has {finds} machine {'find' if finds == 1 else 'finds'}."
            acc.db.notify_once(u["id"], f"cellfind:{cell_id}", "square", text, f"#/explore?cell={cell_id}")

    @router.get("/api/me/export")
    def export(request: Request) -> Response:
        acc, u = me(request)
        body = json.dumps({"exported": _now(), **acc.db.export(u["id"])}, ensure_ascii=False, indent=1)
        return Response(
            body, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="spherex-space-my-data.json"'}
        )

    @router.delete("/api/me", status_code=204)
    def delete_me(request: Request) -> Response:
        acc, u = me(request)
        acc.db.delete_user(u["id"])
        resp = Response(status_code=204)
        resp.delete_cookie(accounts.COOKIE, path="/")
        return resp

    def score_of(acc: accounts.Accounts) -> scoring.Score:
        return scoring.get(st, acc.db)

    @router.patch("/api/me")
    def change_me(body: NameIn, request: Request) -> Any:
        acc, u = me(request)
        if body.name is not None and body.name.strip() and body.name.strip() != u["name"]:
            acc.db.rename(u["id"], body.name.strip())
            score_of(acc).renamed(u["id"])
        if body.hide_from_board is not None:
            score_of(acc)  # the column is made with the score's tables
            acc.db._conn().execute("UPDATE acc_users SET hide_board=? WHERE id=?", (int(body.hide_from_board), u["id"]))
        return {"user": acc.db.user(u["id"])}

    @router.get("/api/me/score")
    def my_score(request: Request) -> Any:
        acc, u = me(request)
        return score_of(acc).summary(u["id"])

    @router.get("/api/leaders")
    def leaders(request: Request, period: str = Query("week", pattern="^(week|month|all)$")) -> Any:
        acc = accounts.get(st)
        u = acc.current_user(request)
        return score_of(acc).leaders(period, u["id"] if u else None)

    # ---------------------------------------------------------------------------------------------------------------------- marks

    @router.get("/api/me/marks")
    def marks(request: Request, field_key: str | None = None) -> Any:
        acc, u = me(request)
        return {"marks": acc.db.marks(u["id"], field_key)}

    @router.post("/api/me/marks")
    def put_mark(body: MarkIn, request: Request) -> Any:
        acc, u = me(request)
        m = body.model_dump(by_alias=True)
        m["id"] = m["id"] or uuid.uuid4().hex
        m["created"] = m["created"] or _now()
        try:
            out = acc.db.put_mark(u["id"], m)
        except LimitReachedError:
            raise HTTPException(409, "too many marks in this account") from None
        if out is None:
            raise HTTPException(409, "this mark id is taken")
        score_of(acc).mark_put(u["id"])
        return out

    @router.delete("/api/me/marks/{mark_id}", status_code=204)
    def delete_mark(mark_id: str, request: Request) -> Response:
        acc, u = me(request)
        if not acc.db.delete_mark(u["id"], mark_id):
            raise HTTPException(404, "no such mark")
        return Response(status_code=204)

    @router.delete("/api/me/marks", status_code=204)
    def delete_mark_q(id: str, request: Request) -> Response:
        return delete_mark(id, request)

    # ---------------------------------------------------------------------------------------------------------------------- tasks

    @router.get("/api/me/tasks")
    def tasks(request: Request) -> Any:
        acc, u = me(request)
        return {"tasks": acc.db.tasks(u["id"])}

    @router.post("/api/me/tasks/{task_id}/done")
    def done(task_id: str, body: DoneIn, request: Request) -> Any:
        acc, u = me(request)
        found = acc.db.task(task_id)
        if found is None or found[1] not in (None, u["id"]):
            raise HTTPException(404, "no such task")
        task = found[0]
        allowed = CELL_RESULTS if task["kind"] == "cell" else FINDING_RESULTS
        if body.result not in allowed:
            raise HTTPException(422, f"result is one of {', '.join(allowed)}")
        already = task["state"] == "done"
        acc.db.claim_task(task_id, u["id"])
        task = acc.db.finish_task(task_id, body.result, body.mark_ids)
        sc = score_of(acc)
        if already:
            points = 0  # a task closed twice counts once
        elif task["kind"] == "finding" and task["finding_id"]:
            acc.db.credit(task["finding_id"], u["id"], "checked", body.result)
            points = sc.answered(u["id"], task["finding_id"], body.result)
        else:
            look = body.model_dump(include={"seconds", "passes_shown", "passes_total", "local_hour"})
            marks = [m for m in acc.db.marks(u["id"]) if m["id"] in set(body.mark_ids)]
            points = sc.square_done(u["id"], task, look, marks)
        return {"task": task, "points": points, "achievements": sc.award(u["id"])}

    # -------------------------------------------------------------------------------------------------------------------- history

    @router.get("/api/me/history")
    def history(request: Request) -> Any:
        acc, u = me(request)
        return {"history": acc.db.history(u["id"])}

    @router.post("/api/me/history")
    def add_history(body: HistoryIn, request: Request) -> Any:
        acc, u = me(request)
        if not REF.match(body.ref):
            raise HTTPException(422, "ref is a place inside this app (it starts with #/)")
        acc.db.add_history(u["id"], body.kind, body.ref, body.title, body.key)
        return {"ok": True}

    @router.delete("/api/me/history", status_code=204)
    def clear_history(request: Request) -> Response:
        acc, u = me(request)
        acc.db.clear_history(u["id"])
        return Response(status_code=204)

    # -------------------------------------------------------------------------------------------------------------- notifications

    @router.get("/api/me/notifications")
    def notifications(request: Request) -> Any:
        acc, u = me(request)
        fresh(acc, u)
        items = acc.db.notifications(u["id"])
        return {"notifications": items, "unread": sum(1 for n in items if not n["read"])}

    @router.post("/api/me/notifications/read")
    def read(body: ReadIn, request: Request) -> Any:
        acc, u = me(request)
        acc.db.read_notifications(u["id"], body.ids)
        return {"ok": True}

    # ------------------------------------------------------------------------------------------------------------- investigations

    @router.get("/api/me/investigations")
    def investigations(request: Request) -> Any:
        acc, u = me(request)
        auto.settle_user(st, acc.db, u["id"])
        return {"investigations": [with_checks(i) for i in acc.db.investigations(u["id"])]}

    @router.post("/api/me/investigations")
    def add_investigation(body: InvestigationIn, request: Request) -> Any:
        acc, u = me(request)
        b = body.model_dump()
        b["notes"], b["frames"] = _stamp(b["notes"]), _stamp(b["frames"])
        try:
            return with_checks(acc.db.add_investigation(u["id"], b))
        except LimitReachedError:
            raise HTTPException(409, "too many investigations in this account") from None

    def patched(acc: accounts.Accounts, u: dict[str, Any], inv_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        inv = acc.db.patch_investigation(u["id"], inv_id, changes)
        if inv is None:
            raise HTTPException(404, "no such investigation")
        return with_checks(inv)

    def mine(acc: accounts.Accounts, u: dict[str, Any], inv_id: str) -> dict[str, Any]:
        inv = acc.db.investigation(u["id"], inv_id)
        if inv is None:
            raise HTTPException(404, "no such investigation")
        return inv

    @router.patch("/api/me/investigations/{inv_id}")
    def patch_investigation(inv_id: str, body: InvestigationPatch, request: Request) -> Any:
        acc, u = me(request)
        changes = body.model_dump(exclude_unset=True)
        if changes.get("title", "") is None:
            raise HTTPException(422, "the title cannot be empty")
        extra = {k: changes.pop(k) for k in ("ruler", "old_survey") if changes.get(k) is not None}
        for k in ("ruler", "old_survey"):
            changes.pop(k, None)
        if extra:
            score_of(acc)  # the column is made with the score's tables
            changes["extra"] = extra
        for k in ("mark_ids", "notes", "frames"):
            if k in changes:
                changes[k] = _stamp(changes[k] or []) if k != "mark_ids" else (changes[k] or [])
        return patched(acc, u, inv_id, changes)

    @router.post("/api/me/investigations/{inv_id}/notes")
    def add_note(inv_id: str, body: Note, request: Request) -> Any:
        acc, u = me(request)
        inv = mine(acc, u, inv_id)
        if len(inv["notes"]) >= MAX_NOTES:
            raise HTTPException(409, "too many notes in this investigation")
        acc.db.patch_investigation(u["id"], inv_id, {"notes": [*inv["notes"], *_stamp([body.model_dump()])]})
        auto.settle(st, acc.db, u["id"], mine(acc, u, inv_id))  # a comment may be all a declared submit was waiting for
        return with_checks(mine(acc, u, inv_id))

    @router.post("/api/me/investigations/{inv_id}/submit")
    def declare(inv_id: str, body: DeclareIn, request: Request) -> Any:
        """ "Check and submit", said once: the checks job is tied to the investigation and the submit finishes by itself."""
        acc, u = me(request)
        inv = mine(acc, u, inv_id)
        if inv["submitted"]:
            return with_checks(inv)
        if not callable(getattr(st, "checks_status", None)):
            raise HTTPException(503, "the checks are not loaded on this server")
        if checks_of(body.checks_job) is None:
            raise HTTPException(404, "no such checks job")
        if body.note and body.note.strip():
            if len(inv["notes"]) >= MAX_NOTES:
                raise HTTPException(409, "too many notes in this investigation")
            acc.db.patch_investigation(u["id"], inv_id, {"notes": [*inv["notes"], *_stamp([{"text": body.note.strip(), "at": None}])]})
        acc.db.declare(u["id"], inv_id, body.checks_job)
        auto.settle(st, acc.db, u["id"], mine(acc, u, inv_id))
        return with_checks(mine(acc, u, inv_id))

    @router.post("/api/me/investigations/{inv_id}/frames")
    def add_frame(inv_id: str, body: Frame, request: Request) -> Any:
        acc, u = me(request)
        inv = mine(acc, u, inv_id)
        if len(inv["frames"]) >= MAX_FRAMES:
            raise HTTPException(409, "too many saved frames in this investigation")
        return patched(acc, u, inv_id, {"frames": [*inv["frames"], *_stamp([body.model_dump()])]})

    @router.delete("/api/me/investigations/{inv_id}", status_code=204)
    def delete_investigation(inv_id: str, request: Request) -> Response:
        acc, u = me(request)
        if not acc.db.delete_investigation(u["id"], inv_id):
            raise HTTPException(404, "no such investigation")
        return Response(status_code=204)

    # ------------------------------------------------------------------------------------------------------------------- findings

    @router.get("/api/me/findings")
    def my_findings(request: Request) -> Any:
        acc, u = me(request)
        return {"findings": acc.db.my_findings(u["id"])}

    @router.post("/api/findings/submit")
    def submit(body: SubmitIn, request: Request) -> Any:
        acc, u = me(request)
        inv = mine(acc, u, body.investigation_id)
        have = acc.db.submission_of(inv["id"])
        if have is not None:
            return {"finding_id": have["finding_id"], "status": have["status"]}
        if not callable(getattr(st, "checks_status", None)):
            raise HTTPException(503, "the checks are not loaded on this server")
        checks = checks_of(body.checks_job)
        if checks is None:
            raise HTTPException(404, "no such checks job")
        if checks.get("state") != "done" or checks.get("can_submit") is not True:
            raise HTTPException(409, "not ready to submit: every check must answer and none may fail")
        rows = [c for c in checks.get("checks") or [] if isinstance(c, dict)]
        if any(c.get("state") == "warn" for c in rows) and not inv["notes"]:
            raise HTTPException(409, "a check gave a warning: add a note to the investigation that explains it")
        sub = auto.submit_now(st, acc.db, u["id"], inv, body.checks_job, checks)
        return {"finding_id": sub["finding_id"], "status": sub["status"]}

    @router.get("/api/findings/submitted")
    def submitted() -> Any:
        return {"findings": accounts.get(st).db.submitted()}

    return router
