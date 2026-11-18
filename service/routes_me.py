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
from service.accounts import agreement, field_people, people
from service.accounts import score as scoring
from service.accounts import share as sharing
from service.accounts import submit as auto
from service.accounts.db import LimitReachedError, iso
from service.accounts.score import POINTS as SCORE_POINTS
from service.ratelimit import Buckets

ID = r"^[A-Za-z0-9_\-:.,+]{6,64}$"  # also the lab's id of a source with no mark: src:<ra>,<dec> (a comma and a sign: M-2, a 422)
REF = re.compile(r"^#/[^\s<>\"']{0,600}$")  # a place inside this app
CELL_RESULTS = ("nothing", "marks")
FINDING_RESULTS = ("moves", "artefact", "known", "cant_tell")  # "Can't tell" closes the task; it is never wrong and never paid
MAX_NOTES, MAX_FRAMES, MAX_THUMB = 200, 60, 200_000


class NameIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    hide_from_board: bool | None = None
    public_profile: bool | None = None  # the public page #/u/<slug>: off until the user turns it on (or shares)


class MarkIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = Field(None, pattern=ID)
    field_key: str = Field(min_length=1, max_length=80)
    cell_id: str | None = Field(None, max_length=40)
    ra: float = Field(ge=0, le=360)
    dec: float = Field(ge=-90, le=90)
    pass_: int | None = Field(None, alias="pass", ge=0, le=100000)
    label: str = Field("", max_length=80)
    level: Literal["suspicious", "interesting", "artefact"] | None = None  # the mark's level of before; from `kind` when not given
    # spec §5g: the kind of the mark (one tap after clicking a point), what an artefact looks like, the exposures it lives in
    kind: Literal["moves", "not_in_catalogues", "artefact", "odd"] | None = None
    look: Literal["blinks", "glare", "spike", "edge", "hot_dot"] | None = None
    exposures: list[str] = Field(default_factory=list, max_length=50)
    note: str = Field("", max_length=2000)
    created: str | None = Field(None, max_length=40)
    mjd: float | None = Field(None, ge=48000.0, le=90000.0)  # the date of the frame the mark was put on (the lab sends it)
    # the first mark of the same mover, when the lab knows a second tap is the same body on another pass (kept and returned only)
    link: str | None = Field(None, pattern=ID)


class TapIn(BaseModel):
    ra: float = Field(ge=0, le=360)
    dec: float = Field(ge=-90, le=90)
    pass_: int | None = Field(None, alias="pass", ge=0, le=10000)
    frame: int | None = Field(None, ge=0, le=100000)
    mjd: float | None = Field(None, ge=48000.0, le=90000.0)
    model_config = ConfigDict(populate_by_name=True)


class FlagIn(BaseModel):
    exposure: str = Field(min_length=1, max_length=160)
    finding_id: str | None = Field(None, max_length=80)
    field_key: str | None = Field(None, max_length=80)
    ra: float | None = Field(None, ge=0, le=360)
    dec: float | None = Field(None, ge=-90, le=90)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DoneIn(BaseModel):
    result: str = Field("", max_length=20)  # not needed with `path`
    mark_ids: list[str] = Field(default_factory=list, max_length=500)
    # what the lab says about the look (the score: a square counts with at least 60 s and every pass shown)
    seconds: float | None = Field(None, ge=0, le=86400)
    passes_shown: int | None = Field(None, ge=0, le=10000)
    passes_total: int | None = Field(None, ge=0, le=10000)
    local_hour: int | None = Field(None, ge=0, le=23)
    # a question series (spec §5e): the answers along it, one per step; for "Tap the pictures where it is missing" the tapped
    # picture indexes (`pictures`); for an anomaly the exposure keys of the odd pictures shown (`exposures`)
    path: list[str] | None = Field(None, max_length=12)
    pictures: list[int] = Field(default_factory=list, max_length=200)
    exposures: list[str] = Field(default_factory=list, max_length=100)
    # find_again / recentre: the places tapped, per pass or per picture
    taps: list[TapIn] = Field(default_factory=list, max_length=200)


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


class Ruler(BaseModel):
    rate: float = Field(ge=0, le=1e7)  # ″/yr measured
    catalogue_rate: float | None = Field(None, ge=0, le=1e7)  # ″/yr of the catalogue, when the object is in one
    pa: float | None = Field(None, ge=0, le=360)  # the direction of the motion, degrees east of north
    shift: float | None = Field(None, ge=0, le=1e6)  # ″ between the two places measured
    object: str | None = Field(None, max_length=120)


class Catalogue(BaseModel):
    source: str = Field(max_length=20)  # gaia | simbad | jpl
    name: str = Field(max_length=120)
    sep: float | None = Field(None, ge=0, le=3600)  # ″ from the object
    type: str | None = Field(None, max_length=60)


class ItemIn(BaseModel):
    """An object the lab adds to an investigation. The lab only gathers; notes and the submit live in the investigation."""

    id: str | None = Field(None, pattern=ID)  # made by the lab to send the same object again (it then replaces the earlier one)
    title: str | None = Field(None, min_length=1, max_length=120)  # the title of a NEW investigation (POST …/investigations/new/items)
    label: str = Field("", max_length=120)
    ra: float = Field(ge=0, le=360)
    dec: float = Field(ge=-90, le=90)
    field_key: str | None = Field(None, max_length=80)
    finding_id: str | None = Field(None, max_length=80)
    mark_ids: list[str] = Field(default_factory=list, max_length=200)
    ruler: Ruler | None = None
    catalogue: Catalogue | None = None
    ref: str | None = Field(None, max_length=600)  # the lab's address of this view
    thumb: str | None = Field(None, max_length=MAX_THUMB)


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
    talk = Buckets(capacity=20, per_second=20 / 3600)  # comments: 20 an hour per person (and one every 10 s, people.COMMENT_GAP_S)

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
        agreement.get(st, acc.db).settle_moves(u["id"])
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
        if body.public_profile is not None:
            sharing.get(st, acc.db).set_public(u["id"], body.public_profile)
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
        # each mark that acted says where it ended: known (what), no match in any catalogue, set aside, or could not be checked yet
        verdicts = field_people.get(st, acc.db).verdicts(u["id"])
        return {
            "marks": [{**m, **({"verdict": verdicts[m["id"]]} if m["id"] in verdicts else {})} for m in acc.db.marks(u["id"], field_key)]
        }

    @router.post("/api/me/marks")
    async def put_mark(body: MarkIn, request: Request) -> Any:
        """Keep a mark; a mark of a kind acts (spec §5g): it moves → the checks; not in the catalogues → every catalogue again;
        artefact → set aside with a second person; odd → the Talk of the place. What happened comes back as `effect`."""
        acc, u = me(request)
        if body.kind == "odd" and not body.note.strip():
            raise HTTPException(422, "say in a few words what is odd")
        if body.kind != "artefact" and body.look is not None:
            raise HTTPException(422, "what it looks like goes with an artefact mark")
        m = body.model_dump(by_alias=True)
        m["id"] = m["id"] or uuid.uuid4().hex
        m["created"] = m["created"] or _now()
        m["level"] = m["level"] or ("artefact" if body.kind == "artefact" else "suspicious")
        try:
            out = acc.db.put_mark(u["id"], m)
        except LimitReachedError:
            raise HTTPException(409, "too many marks in this account") from None
        if out is None:
            raise HTTPException(409, "this mark id is taken")
        score_of(acc).mark_put(u["id"])
        if body.kind:
            out["effect"] = await field_people.get(st, acc.db).after_mark(u["id"], out)
        return out

    @router.get("/api/field/{key}/people")
    def field_layer(key: str, request: Request) -> Any:
        """The layer "People's marks" (public): every person's marks on this field merged by place and kind, with counts and never
        a name, and the people's answers on the machine finds here; what was said at a place only after you marked or answered there."""
        u = accounts.get(st).current_user(request)
        return field_people.get(st, accounts.get(st).db).field(key, u["id"] if u else None)

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

    def series_done(acc: accounts.Accounts, u: dict[str, Any], task_id: str, task: dict[str, Any], series: str, body: DoneIn) -> Any:
        if task["state"] == "done":
            return {
                "task": task,
                "points": 0,
                "achievements": [],
                "result": {"id": task["result"], "label": people.label_of(series, task["result"])},
            }
        from service.routes_tasks import find_row

        row = find_row(st, task["finding_id"]) or {"finding_id": task["finding_id"], "ra": task.get("ra"), "dec": task.get("dec")}
        try:
            result, points = people.get(st, acc.db).answer(u["id"], series, row, list(body.path or []), body.pictures, body.exposures)
        except people.AnswerError as e:
            raise HTTPException(422, str(e)) from None
        acc.db.claim_task(task_id, u["id"])
        done = acc.db.finish_task(task_id, result, body.mark_ids)
        return {"task": done, "points": points, "achievements": scoring.get(st, acc.db).award(u["id"]),
                "result": {"id": result, "label": people.label_of(series, result)},
                "agreement": agreement_of(acc, series, task["finding_id"])}  # fmt: skip

    def agreement_of(acc: accounts.Accounts, series: str, finding_id: str) -> dict[str, Any]:
        """Where the people's answers on this find stand: how many answered (controls apart), how many are needed, and the settled
        result once there is one. Points for an answer are paid when the find settles (a control object's at once)."""
        c = acc.db._conn()
        n = c.execute(
            "SELECT COUNT(*) FROM acc_series_answers WHERE series=? AND finding_id=? AND control=0", (series, finding_id)
        ).fetchone()[0]
        settled = c.execute("SELECT result FROM acc_series_results WHERE series=? AND finding_id=?", (series, finding_id)).fetchone()
        return {
            "answers": int(n),
            "needed": int(people.AGREE_WEIGHT),
            "max_answers": int(people.MAX_ANSWERS),
            "settled": settled is not None,
            "result": None if settled is None or settled[0] is None else {"id": settled[0], "label": people.label_of(series, settled[0])},
            "points_when_agreed": int(SCORE_POINTS["check"]),
        }

    def other_done(acc: accounts.Accounts, u: dict[str, Any], task_id: str, task: dict[str, Any], body: DoneIn) -> Any:
        kind, ref = task["kind"], str(task.get("finding_id") or "")
        allowed = {"find_again": ("seen", "not_seen"), "recentre": ("done",), "bad_picture": ("broken", "fine"),
                   "second_look": ("moves", "artefact")}[kind] + (people.CANT_TELL,)  # fmt: skip
        if body.result not in allowed:
            raise HTTPException(422, f"result is one of {', '.join(allowed)}")
        p = people.get(st, acc.db)
        if task["state"] != "done":
            if kind in ("find_again", "recentre") and body.result in ("seen", "done"):
                if not body.taps:
                    raise HTTPException(422, "tap the object first")
                p.tap(kind, ref, u["id"], [t.model_dump(by_alias=True) for t in body.taps])
            elif kind == "bad_picture" and body.result in ("broken", "fine"):
                p.flag(u["id"], ref, body.result, "bad_picture", ra=task.get("ra"), dec=task.get("dec"), field_key=task.get("field_key"))
            elif kind == "second_look" and body.result != people.CANT_TELL:
                scoring.get(st, acc.db).answered(u["id"], ref, body.result)  # judged by the verdict; counts for Peer and Referee
        acc.db.claim_task(task_id, u["id"])
        done = acc.db.finish_task(task_id, body.result, body.mark_ids)
        return {"task": done, "points": 0, "achievements": scoring.get(st, acc.db).award(u["id"])}

    @router.post("/api/me/exposures/flag")
    def flag_exposure(body: FlagIn, request: Request) -> Any:
        """ "This picture is broken", from any picture in the lab. Left out of every stack and spectrum after two distinct people."""
        acc, u = me(request)
        n = people.get(st, acc.db).flag(u["id"], body.exposure, "broken", "flag", finding_id=body.finding_id, field_key=body.field_key,
                                        ra=body.ra, dec=body.dec)  # fmt: skip
        return {"exposure": body.exposure, "flags": n, "bad": n >= people.BAD_FLAGS}

    @router.get("/api/findings/{finding_id}/comments")
    def comments(finding_id: str, request: Request) -> Any:
        """Talk under a finding (public): plain text and links; the author's public name only."""
        u = accounts.get(st).current_user(request)
        return {"comments": people.get(st, accounts.get(st).db).comments(finding_id, u["id"] if u else None)}

    @router.post("/api/me/findings/{finding_id}/comments")
    def add_comment(finding_id: str, body: CommentIn, request: Request) -> Any:
        acc, u = me(request)
        if not re.fullmatch(r"[A-Za-z0-9_\-:.]{1,80}", finding_id):
            raise HTTPException(422, "this is not a finding id")
        wait = talk.take(u["id"])
        if wait > 0:
            raise HTTPException(429, "too many comments: wait a little", headers={"Retry-After": str(int(wait) + 1)})
        try:
            return people.get(st, acc.db).add_comment(u["id"], finding_id, body.text)
        except people.AnswerError as e:
            raise HTTPException(422, str(e)) from None

    @router.delete("/api/me/comments/{comment_id}", status_code=204)
    def delete_comment(comment_id: int, request: Request) -> Response:
        acc, u = me(request)
        if not people.get(st, acc.db).delete_comment(u["id"], comment_id):
            raise HTTPException(404, "no such comment of yours")
        return Response(status_code=204)

    @router.post("/api/me/comments/{comment_id}/report")
    def report_comment(comment_id: int, request: Request) -> Any:
        acc, u = me(request)
        hidden = people.get(st, acc.db).report(u["id"], comment_id)
        if hidden is None:
            raise HTTPException(404, "no such comment, or it is yours")
        return {"reported": True}

    @router.get("/api/people/counts")
    def counts() -> Any:
        """For the Method page: how often each answer was given, and the answers that go against a rule of the machine."""
        db = accounts.get(st).db
        return {**people.get(st, db).counts(), "field": field_people.get(st, db).counts()}

    @router.post("/api/me/tasks/{task_id}/done")
    def done(task_id: str, body: DoneIn, request: Request) -> Any:
        acc, u = me(request)
        found = acc.db.task(task_id)
        if found is None or found[1] not in (None, u["id"]):
            raise HTTPException(404, "no such task")
        task = found[0]
        series = (
            people.series_of(str(task.get("finding_id") or "").split(":")[0])
            if task["kind"] in ("moves", "red", "anomaly", "finding")
            else None
        )
        if body.path is not None and series is not None:  # a question series (spec §5e): the path decides
            return series_done(acc, u, task_id, task, series, body)
        if task["kind"] in people.TASK_KINDS:
            return other_done(acc, u, task_id, task, body)
        if task["kind"] in agreement.KINDS:
            allowed: tuple[str, ...] = tuple(x for x, _ in agreement.ANSWERS[task["kind"]])
        else:
            allowed = CELL_RESULTS if task["kind"] == "cell" else FINDING_RESULTS
        if body.result not in allowed:
            raise HTTPException(422, f"result is one of {', '.join(allowed)}")
        already = task["state"] == "done"
        acc.db.claim_task(task_id, u["id"])
        task = acc.db.finish_task(task_id, body.result, body.mark_ids)
        sc = score_of(acc)
        if already:
            points = 0  # a task closed twice counts once
        elif task["kind"] in agreement.KINDS and task["finding_id"]:
            points = agreement.get(st, acc.db).answer(u["id"], task["kind"], task["finding_id"], body.result, task)
        elif task["kind"] == "finding" and task["finding_id"]:
            if body.result != "cant_tell":
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

    @router.post("/api/me/investigations/{inv_id}/items")
    def add_item(inv_id: str, body: ItemIn, request: Request) -> Any:
        """The lab adds an object to an investigation: `inv_id` is an investigation of the user, or `new` (made with `title`)."""
        acc, u = me(request)
        item = body.model_dump(exclude={"title"}, exclude_none=True)
        item["id"] = item.get("id") or uuid.uuid4().hex
        item["at"] = _now()
        if item.get("ref") and not REF.match(item["ref"]):
            raise HTTPException(422, "ref is a place inside this app (it starts with #/)")
        if item.get("thumb") and not str(item["thumb"]).startswith("data:image/"):
            raise HTTPException(422, "thumb is a small picture as a data URL")
        try:
            if inv_id == "new":
                title = body.title or body.label or f"Object at RA {body.ra:.4f}° Dec {body.dec:+.4f}°"
                inv_id = acc.db.add_investigation(u["id"], {"title": title, "finding_id": body.finding_id})["id"]
            elif mine(acc, u, inv_id)["submitted"]:
                raise HTTPException(409, "this investigation is submitted: nothing more is added to it")
            inv = acc.db.add_item(u["id"], inv_id, item)
        except LimitReachedError:
            raise HTTPException(409, "too many investigations, or too many objects in this one") from None
        if inv is None:
            raise HTTPException(404, "no such investigation")
        return with_checks(inv)

    @router.delete("/api/me/investigations/{inv_id}/items/{item_id}")
    def remove_item(inv_id: str, item_id: str, request: Request) -> Any:
        acc, u = me(request)
        if mine(acc, u, inv_id)["submitted"]:
            raise HTTPException(409, "this investigation is submitted")
        inv = acc.db.remove_item(u["id"], inv_id, item_id)
        if inv is None:
            raise HTTPException(404, "no such object in this investigation")
        return with_checks(inv)

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
