# ruff: noqa: E501
"""People's marks are real input, shared on the field (product spec §5g).

A mark has a kind (one tap after clicking a point):
  moves              marks of one person on two passes (or two visits of one pass) → a real checks job (§6, with the linking of the
                     track search) on an investigation made for them; when every check answers without a fail the finding is
                     submitted by itself, "found by people", with the credit; when not, the person is told why (service.accounts.submit)
  not_in_catalogues  a steady source with no ring → every catalogue is asked again at that place, the deep ones too; when none has
                     anything within MATCH_ARCSEC, it becomes a finding "unknown source, found by people", and it goes to the
                     brown-dwarf search's queue for the red-source / brown-dwarf check on its spectrum
  artefact           with what it looks like (LOOKS) → when two people mark an artefact at one place (within SAME_PLACE_ARCSEC), the
                     machine's finds there are set aside ("people say: artefact, 2 of 2"): set_aside(st); the exposures the mark
                     names get a bad-picture flag from each person (service.accounts.people: bad after two)
  odd                with a short note → the note goes to the Talk of that place (the machine find there, else the place itself)

Shared on the field: GET /api/field/{key}/people merges every person's marks there by place and kind, with counts and never a name;
the people's answers on the machine finds there are shown too. What was said at a place is shown to a person only after they have
marked or answered there themselves (so they are not led): before that, only that people have marked it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from service.accounts import people
from service.accounts.db import AccountsDB, iso

KINDS = ("moves", "not_in_catalogues", "artefact", "odd")
LOOKS = {
    "blinks": "blinks on and off",
    "glare": "glare or ghost of a bright star",
    "spike": "spike",
    "edge": "picture edge",
    "hot_dot": "a single hot dot",
}
KIND_LABEL = {"moves": "it moves", "not_in_catalogues": "not in the catalogues", "artefact": "artefact", "odd": "something odd"}
SAME_PLACE_ARCSEC = 6.0  # marks this close are one place
# one person's "it moves" marks are one moving object when they are on different passes (or visits) and no farther apart than a body
# can travel in the time between them: LINK_BASE_ARCSEC plus a rate. Two marks on the same pass and visit are two objects.
LINK_BASE_ARCSEC = 30.0  # the marks' own scatter (a click is good to a few pixels)
LINK_VISIT_ARCSEC_H = 150.0  # visits of one pass (dated marks, hours apart): up to a near-Earth asteroid's rate
LINK_PASS_ARCSEC_YR = 120.0  # passes months apart: up to the fastest nearby stars and slow distant bodies
LINK_UNDATED_ARCSEC = 60.0  # two passes without dates: a slow mover
LINK_MAX_ARCSEC = 600.0
MATCH_ARCSEC = 3.0  # a catalogue source this close to a "not in the catalogues" mark is the source
ARTEFACT_AGREE = 2  # distinct people marking an artefact at one place set the machine's finds there aside
LOOKUP_SIZE_ARCMIN = 1.0  # the patch asked of the catalogues around a "not in the catalogues" mark
LOOKUP_EPOCH = 2025.9
SINGLE_JPL_ARCSEC = 12.0  # a known body this close to one dated "it moves" mark, at that moment, is the marked point (the checks' radius)
SINGLE_JPL_SIZE_ARCMIN = 2.0  # the patch JPL is asked about around it
RETRY_STEPS_S = (120.0, 600.0, 1800.0, 3600.0)  # a catalogue that did not answer is asked again after these, then every hour

SCHEMA = """
CREATE TABLE IF NOT EXISTS acc_people_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  user_id TEXT NOT NULL,
  field_key TEXT NOT NULL,
  ra REAL NOT NULL,
  dec REAL NOT NULL,
  mark_ids TEXT NOT NULL,
  state TEXT NOT NULL,
  investigation_id TEXT,
  finding_id TEXT,
  data TEXT NOT NULL DEFAULT '{}',
  at REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acc_people_jobs_user ON acc_people_jobs(user_id, kind);
"""


def sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Arcseconds between two places."""
    a1, d1, a2, d2 = (math.radians(x) for x in (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((a2 - a1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600.0


def field_box(key: str) -> tuple[float, float, float] | None:
    """(ra, dec, fov°) of a lab field key "ra,dec,fov′", or None for a store key."""
    try:
        ra, dec, fov = (float(x) for x in key.split(","))
    except ValueError:
        return None
    return ra, dec, fov / 60.0


def in_box(box: tuple[float, float, float], ra: float, dec: float) -> bool:
    ra0, dec0, size = box
    half = size / 2 + 1e-6
    return abs(dec - dec0) <= half and abs((ra - ra0 + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec0)) <= half


def clusters(points: list[dict[str, Any]], radius: float) -> list[list[dict[str, Any]]]:
    """Single-linkage groups of points within `radius` arcseconds of each other."""
    groups: list[list[dict[str, Any]]] = []
    for p in points:
        near = [g for g in groups if any(sep(p["ra"], p["dec"], q["ra"], q["dec"]) <= radius for q in g)]
        merged = [p, *[q for g in near for q in g]]
        groups = [g for g in groups if g not in near] + [merged]
    return groups


def mover_linked(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two "it moves" marks of one person are one body: on different passes or visits, and within what a body travels between them."""
    ta, tb = a.get("mjd"), b.get("mjd")
    if a.get("pass") == b.get("pass") and (ta is None or tb is None or abs(float(ta) - float(tb)) < 1e-4):
        return False  # the same picture: two different objects
    if ta is not None and tb is not None:
        dt_d = abs(float(ta) - float(tb))
        reach = LINK_BASE_ARCSEC + (LINK_VISIT_ARCSEC_H * dt_d * 24.0 if dt_d < 5.0 else LINK_PASS_ARCSEC_YR * dt_d / 365.25)
    else:
        reach = LINK_UNDATED_ARCSEC
    return sep(a["ra"], a["dec"], b["ra"], b["dec"]) <= min(LINK_MAX_ARCSEC, reach)


def mover_groups(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """One person's "it moves" marks in a field, grouped into bodies by `mover_linked` (chained)."""
    groups: list[list[dict[str, Any]]] = []
    for p in points:
        near = [g for g in groups if any(mover_linked(p, q) for q in g)]
        merged = [p, *[q for g in near for q in g]]
        groups = [g for g in groups if g not in near] + [merged]
    return groups


class FieldPeople:
    def __init__(self, st: Any, db: AccountsDB) -> None:
        self.st = st
        self.db = db
        self.people = people.get(st, db)
        db._conn().executescript(SCHEMA)

    # ------------------------------------------------------------------------------------------------------------ the effects

    async def after_mark(self, user_id: str, mark: dict[str, Any]) -> dict[str, Any]:
        """Act on a mark of a kind; returns what happened ({effect, state, …}) for the lab's status line."""
        kind = mark.get("kind")
        if kind == "moves":
            return await self._moves(user_id, mark)
        if kind == "not_in_catalogues":
            return await self._lookup(user_id, mark)
        if kind == "artefact":
            return self._artefact(user_id, mark)
        if kind == "odd":
            return self._odd(user_id, mark)
        return {}

    async def _moves(self, user_id: str, mark: dict[str, Any]) -> dict[str, Any]:
        c = self.db._conn()
        taken = {
            m
            for r in c.execute("SELECT mark_ids FROM acc_people_jobs WHERE user_id=? AND kind='moves'", (user_id,))
            for m in json.loads(r[0])
        }
        mine = [m for m in self.db.marks(user_id, mark["field_key"]) if m.get("kind") == "moves"]
        group = next((g for g in mover_groups(mine) if any(x["id"] == mark["id"] for x in g)), [mark])
        if any(x["id"] in taken for x in group):
            return {"effect": "moves", "state": "checking"}  # this object is being checked already
        dates = {(x.get("pass"), round(float(x["mjd"]), 4) if x.get("mjd") is not None else None) for x in group}
        passes = {x.get("pass") for x in group}
        if len(passes) < 2 and len(dates) < 2:
            # one dated mark: JPL says at once whether a known body was there then (a known asteroid is named without a second
            # mark); a second mark on another pass or visit starts the checks of its motion
            return await self._single(user_id, mark)
        group.sort(key=lambda x: (x.get("pass") if x.get("pass") is not None else -1, x.get("mjd") or 0.0))
        first = group[0]
        request = {
            "field_key": mark["field_key"], "ra": round(first["ra"], 6), "dec": round(first["dec"], 6),
            "marks": [{"pass": x.get("pass") or 0, "ra": round(x["ra"], 6), "dec": round(x["dec"], 6), **({"mjd": round(float(x["mjd"]), 5)} if x.get("mjd") is not None else {})}
                      for x in group],
        }  # fmt: skip
        title = f"It moves: RA {first['ra']:.4f}° Dec {first['dec']:+.4f}° (found by people)"
        inv = self.db.add_investigation(user_id, {"title": title, "mark_ids": [x["id"] for x in group]})
        self.db.add_item(user_id, inv["id"], {"id": "moves", "label": "Marked as moving", "ra": first["ra"], "dec": first["dec"], "field_key": mark["field_key"],
                                               "mark_ids": [x["id"] for x in group], "at": iso(time.time())})  # fmt: skip
        job = await self._start_checks(request)
        jid = f"moves:{inv['id']}"
        now = time.time()
        c.execute(
            "INSERT INTO acc_people_jobs(id,kind,user_id,field_key,ra,dec,mark_ids,state,investigation_id,data,at,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, "moves", user_id, mark["field_key"], first["ra"], first["dec"], json.dumps([x["id"] for x in group]), "checking" if job else "no_checks",
             inv["id"], json.dumps({"checks_job": job}), now, now),
        )  # fmt: skip
        if job is None:
            self.db.notify(user_id, "checks", f"“{title}”: the checks could not be started here. They start when you press Check and submit.",
                           f"#/me?tab=investigations&inv={inv['id']}")  # fmt: skip
            return {"effect": "moves", "state": "no_checks", "investigation_id": inv["id"]}
        from service.accounts import submit

        self.db.declare(user_id, inv["id"], job)  # the submit finishes by itself when every check has answered without a fail
        fresh = self.db.investigation(user_id, inv["id"])
        state = submit.settle(self.st, self.db, user_id, fresh) if fresh else "checking"
        return {"effect": "moves", "state": state, "investigation_id": inv["id"], "checks_job": job}

    async def _single(self, user_id: str, mark: dict[str, Any]) -> dict[str, Any]:
        """One dated "it moves" mark: JPL is asked for a known Solar System body at that place at that moment."""
        if mark.get("mjd") is None:
            return {"effect": "moves", "state": "needs_another_pass"}
        c = self.db._conn()
        jid = f"single:{mark['id']}"
        now = time.time()
        row = c.execute("SELECT data FROM acc_people_jobs WHERE id=?", (jid,)).fetchone()
        tries = int(json.loads(row[0] or "{}").get("tries", 0)) if row else 0
        c.execute(
            "INSERT OR IGNORE INTO acc_people_jobs(id,kind,user_id,field_key,ra,dec,mark_ids,state,at,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (jid, "single", user_id, mark["field_key"], mark["ra"], mark["dec"], json.dumps([mark["id"]]), "asking", now, now),
        )
        got = await self._jpl_at(mark)
        if got is None or got.get("state") != "ok":
            retry_at = now + RETRY_STEPS_S[min(tries, len(RETRY_STEPS_S) - 1)]
            data = {"tries": tries + 1, "retry_at": retry_at, "why": (got or {}).get("error") or (got or {}).get("state") or "not loaded"}
            c.execute("UPDATE acc_people_jobs SET state='jpl_wait', data=?, updated=? WHERE id=?", (json.dumps(data), time.time(), jid))
            return {"effect": "moves", "state": "jpl_wait", "retry_at": iso(retry_at)}
        near = []
        for body in got.get("rows") or []:
            d = sep(mark["ra"], mark["dec"], float(body[1]), float(body[2]))
            if d <= SINGLE_JPL_ARCSEC:
                near.append((d, str(body[0]), body[3]))
        if near:
            d, name, v = min(near)
            data = {"tries": tries + 1, "name": name, "sep": round(d, 1), "v": v}
            c.execute("UPDATE acc_people_jobs SET state='known', data=?, updated=? WHERE id=?", (json.dumps(data), time.time(), jid))
            self.db.notify(
                user_id, "checks", f"Your mark at RA {mark['ra']:.4f}° Dec {mark['dec']:+.4f}° is a known body: {name} (JPL).", None
            )
            return {"effect": "moves", "state": "known", "name": name, "sep": round(d, 1)}
        data = {"tries": tries + 1, "bodies": len(got.get("rows") or [])}
        c.execute("UPDATE acc_people_jobs SET state='no_body', data=?, updated=? WHERE id=?", (json.dumps(data), time.time(), jid))
        return {"effect": "moves", "state": "needs_another_pass", "jpl": "no_body"}

    async def _jpl_at(self, mark: dict[str, Any]) -> dict[str, Any] | None:
        """JPL's bodies around a mark at its moment, or None where there is no catalogue service (a test puts its own `jpl_at`)."""
        hook = getattr(self.st, "jpl_at", None)
        if callable(hook):
            got = hook(mark["ra"], mark["dec"], float(mark["mjd"]))
            return dict(await got if asyncio.iscoroutine(got) else got)
        try:
            from service.routes_known_field import known_field_of

            kf = known_field_of(self.st)
        except Exception:
            return None
        try:
            return await kf.jpl_at(round(mark["ra"], 4), round(mark["dec"], 4), SINGLE_JPL_SIZE_ARCMIN, round(float(mark["mjd"]), 5), 20.0)
        except Exception as e:  # said as "not answered"; asked again later
            return {"state": "unavailable", "error": f"{type(e).__name__}"}

    def backfill(self) -> int:
        """Once at start: every dated "it moves" mark that never got an answer (made before the single check existed) gets one, asked by
        the next retry_due. Returns how many were queued."""
        c = self.db._conn()
        have = {m for r in c.execute("SELECT mark_ids FROM acc_people_jobs") for m in json.loads(r[0] or "[]")}
        n = 0
        now = time.time()
        for r in c.execute("SELECT id, user_id, field_key, ra, dec FROM acc_marks WHERE kind='moves' AND mjd IS NOT NULL").fetchall():
            if r["id"] in have:
                continue
            c.execute(
                "INSERT OR IGNORE INTO acc_people_jobs(id,kind,user_id,field_key,ra,dec,mark_ids,state,data,at,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f"single:{r['id']}", "single", r["user_id"], r["field_key"], r["ra"], r["dec"], json.dumps([r["id"]]), "jpl_wait",
                 json.dumps({"tries": 0, "retry_at": now}), now, now),
            )  # fmt: skip
            n += 1
        return n

    async def retry_due(self) -> int:
        """Ask again what did not answer and whose time has come: a single mark's JPL, a "not in the catalogues" lookup. Returns how
        many were asked (the service's loop calls it every minute; it survives restarts, the jobs are in the database)."""
        c = self.db._conn()
        now = time.time()
        n = 0
        rows = c.execute(
            "SELECT id, kind, user_id, mark_ids, state, data, updated FROM acc_people_jobs WHERE state IN ('jpl_wait','no_answer')"
        ).fetchall()
        for r in rows:
            data = json.loads(r["data"] or "{}")
            tries = int(data.get("tries", 1))
            due = (
                float(data["retry_at"])
                if data.get("retry_at") is not None
                else float(r["updated"]) + RETRY_STEPS_S[min(max(tries - 1, 0), len(RETRY_STEPS_S) - 1)]
            )
            if due > now:
                continue
            ids = json.loads(r["mark_ids"] or "[]")
            mark = next((m for m in self.db.marks(r["user_id"]) if ids and m["id"] == ids[0]), None)
            if mark is None:
                continue
            if r["kind"] == "single":
                await self._single(r["user_id"], mark)
            elif r["kind"] == "lookup":
                c.execute("UPDATE acc_people_jobs SET data=? WHERE id=?", (json.dumps({**data, "tries": tries + 1}), r["id"]))
                await self._lookup(r["user_id"], mark)
            n += 1
        return n

    def verdicts(self, user_id: str) -> dict[str, dict[str, Any]]:
        """{mark id: verdict} for every mark of this user that acted: {state, text, name?, retry_at?, investigation_id?}. Every mark ends
        with one: known (and what), no match in any catalogue, set aside as an artefact, or could not be checked yet (asked again at …)."""
        c = self.db._conn()
        out: dict[str, dict[str, Any]] = {}
        jobs = c.execute("SELECT * FROM acc_people_jobs WHERE user_id=? ORDER BY updated", (user_id,)).fetchall()
        for j in jobs:
            v = self._verdict_of(dict(j))
            if v is not None:
                for mid in json.loads(j["mark_ids"] or "[]"):
                    out[mid] = v
        return out

    def _verdict_of(self, j: dict[str, Any]) -> dict[str, Any] | None:
        data = json.loads(j.get("data") or "{}")
        st, kind = j["state"], j["kind"]

        def waiting(what: str) -> dict[str, Any]:
            at = data.get("retry_at")
            when = f"; asked again at {datetime.fromtimestamp(float(at), UTC):%H:%M} UTC" if at else "; asked again soon"
            return {
                "state": "waiting",
                "text": f"Could not be checked yet: {what} did not answer{when}.",
                **({"retry_at": iso(float(at))} if at else {}),
            }

        if kind == "single":
            if st == "known":
                return {
                    "state": "known",
                    "name": data.get("name"),
                    "text": f"Known: {data.get('name')}, a Solar System body JPL puts {data.get('sep')}″ from your mark at that moment.",
                }
            if st == "jpl_wait":
                return waiting("JPL")
            if st == "no_body":
                return {
                    "state": "needs_another_pass",
                    "text": "JPL has no known body there at that moment. Mark it in another visit or pass: then its motion is checked.",
                }
            return {"state": "checking", "text": "Asking JPL…"}
        if kind == "lookup":
            if st == "known":
                return {
                    "state": "known",
                    "name": (data.get("matches") or [None])[0],
                    "text": f"Known: {', '.join(data.get('matches') or [])[:120]}.",
                }
            if st == "unknown":
                return {
                    "state": "no_match",
                    "text": "No match in any catalogue: it is a find, submitted under your name.",
                    "finding_id": j.get("finding_id"),
                }
            if st == "no_answer":
                return waiting(", ".join(data.get("silent") or []) or "the catalogues")
            return {"state": "checking", "text": "Asking the catalogues…"}
        if kind == "moves":
            if st == "no_checks":
                return {
                    "state": "waiting",
                    "text": "The checks could not start here: press Check and submit in the Cabinet.",
                    "investigation_id": j.get("investigation_id"),
                }
            job = data.get("checks_job")
            view = None
            hook = getattr(self.st, "checks_status", None)
            if job and callable(hook):
                with contextlib.suppress(Exception):
                    view = hook(job)
            if not view:
                return {"state": "checking", "text": "Checking…", "investigation_id": j.get("investigation_id")}
            checks = {c["id"]: c for c in view.get("checks") or []}
            failed = [c for c in checks.values() if c.get("state") == "fail"]
            jpl = checks.get("jpl") or {}
            if jpl.get("state") == "fail":
                return {
                    "state": "known",
                    "text": str(jpl.get("detail") or "A known Solar System body (JPL)."),
                    "investigation_id": j.get("investigation_id"),
                }
            if failed:
                return {
                    "state": "known",
                    "text": str(failed[0].get("detail") or "A catalogue explains it."),
                    "investigation_id": j.get("investigation_id"),
                }
            if view.get("next_try_at") and any(c.get("state") == "no_answer" for c in checks.values()):
                who = ", ".join(cid.upper() if cid == "jpl" else cid for cid, c in checks.items() if c.get("state") == "no_answer")
                return {
                    **waiting(who),
                    "retry_at": view["next_try_at"],
                    "text": f"Could not be checked yet: {who} did not answer; asked again at {view['next_try_at'][11:16]} UTC.",
                }
            if view.get("state") == "done" and view.get("can_submit"):
                return {
                    "state": "no_match",
                    "text": "No match in any catalogue: submitted, waiting for the verdict.",
                    "investigation_id": j.get("investigation_id"),
                }
            if view.get("state") == "done" and view.get("needs_comment"):
                return {
                    "state": "comment",
                    "text": "The checks answered with a warning: one comment is needed (Cabinet).",
                    "investigation_id": j.get("investigation_id"),
                }
            return {"state": "checking", "text": "Checking…", "investigation_id": j.get("investigation_id")}
        return None

    async def _start_checks(self, request: dict[str, Any]) -> str | None:
        """The checks of §6 on these marks: the checks stream's job, or None where it is not loaded."""
        hook = getattr(self.st, "start_checks", None)  # a test puts its own here
        if callable(hook):
            got = hook(request)
            return str(await got if asyncio.iscoroutine(got) else got) if got else None
        try:
            from service.routes_checks import checks_of
        except ImportError:
            return None
        try:
            return str(checks_of(self.st).start(request).id)
        except Exception:  # the checks are an extra here: the mark is kept, the person can still press Check and submit
            return None

    async def _lookup(self, user_id: str, mark: dict[str, Any]) -> dict[str, Any]:
        """Every catalogue again at the place, the deep ones too."""
        c = self.db._conn()
        jid = f"lookup:{mark['id']}"
        now = time.time()
        c.execute(
            "INSERT OR IGNORE INTO acc_people_jobs(id,kind,user_id,field_key,ra,dec,mark_ids,state,at,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (jid, "lookup", user_id, mark["field_key"], mark["ra"], mark["dec"], json.dumps([mark["id"]]), "asking", now, now),
        )
        got = await self._ask_catalogues(mark["ra"], mark["dec"])
        if got is None:
            c.execute("UPDATE acc_people_jobs SET state='no_answer', updated=? WHERE id=?", (time.time(), jid))
            return {"effect": "not_in_catalogues", "state": "no_answer"}
        near = [x for x in got["sources"] if sep(mark["ra"], mark["dec"], x["ra"], x["dec"]) <= MATCH_ARCSEC]
        if near:
            names = [str(x.get("name") or x.get("id")) for x in near]
            c.execute(
                "UPDATE acc_people_jobs SET state='known', data=?, updated=? WHERE id=?",
                (json.dumps({"matches": names[:5]}), time.time(), jid),
            )
            self.db.notify(
                user_id,
                "lookup",
                f"The source you marked at RA {mark['ra']:.4f}° Dec {mark['dec']:+.4f}° is in a catalogue: {names[0]}.",
                None,
            )
            return {"effect": "not_in_catalogues", "state": "known", "matches": names[:5]}
        if not got["answered"]:
            c.execute(
                "UPDATE acc_people_jobs SET state='no_answer', data=?, updated=? WHERE id=?",
                (json.dumps({"silent": got["silent"]}), time.time(), jid),
            )
            return {"effect": "not_in_catalogues", "state": "no_answer", "silent": got["silent"]}
        # nothing in any catalogue: an unknown source, found by people
        title = f"Unknown source, found by people: RA {mark['ra']:.4f}° Dec {mark['dec']:+.4f}°"
        inv = self.db.add_investigation(user_id, {"title": title, "mark_ids": [mark["id"]]})
        checks = [{"id": cat, "state": "pass", "detail": f"Nothing within {MATCH_ARCSEC:g}″ in {cat}."} for cat in got["asked"]]
        sub = self.db.submit(
            user_id, inv, f"lookup:{mark['id']}", {"field_key": mark["field_key"], "ra": mark["ra"], "dec": mark["dec"]}, checks
        )
        self.db.credit(sub["finding_id"], user_id, "marked", mark["id"])
        c.execute(
            "INSERT OR IGNORE INTO acc_bd_queue(finding_id,ra,dec,agree,total,at) VALUES(?,?,?,?,?,?)",
            (sub["finding_id"], mark["ra"], mark["dec"], 1, 1, time.time()),
        )  # the brown-dwarf search checks its spectrum (a red source, a cold brown dwarf?)
        c.execute(
            "UPDATE acc_people_jobs SET state='unknown', finding_id=?, investigation_id=?, data=?, updated=? WHERE id=?",
            (sub["finding_id"], inv["id"], json.dumps({"asked": got["asked"]}), time.time(), jid),
        )
        return {"effect": "not_in_catalogues", "state": "unknown", "finding_id": sub["finding_id"]}

    async def _ask_catalogues(self, ra: float, dec: float) -> dict[str, Any] | None:
        """{sources: [{ra, dec, name}], answered: bool, asked: [catalogues], silent: [catalogues that did not answer]} or None
        where no catalogue service is loaded."""
        hook = getattr(self.st, "lookup_place", None)  # a test puts its own here
        if callable(hook):
            got = hook(ra, dec)
            return dict(await got if asyncio.iscoroutine(got) else got)
        try:
            from service.routes_known_field import known_field_of
        except ImportError:
            return None
        try:
            ans = await known_field_of(self.st).objects(ra, dec, LOOKUP_SIZE_ARCMIN, [LOOKUP_EPOCH], wait_s=None)
        except Exception:
            return None
        sources = []
        for x in [*(ans.get("objects") or []), *(ans.get("match") or [])]:
            pos = (x.get("positions") or [{}])[0]
            if pos.get("ra") is not None:
                sources.append({"ra": float(pos["ra"]), "dec": float(pos["dec"]), "name": x.get("name") or x.get("id")})
        status = ans.get("status") or {}
        parts = {
            "gaia": status.get("gaia"),
            "simbad": status.get("simbad"),
            **{f"deep:{k}": v for k, v in ((status.get("match") or {}).items())},
        }
        silent = [k for k, v in parts.items() if not v or v.get("state") not in ("ok", "none", "empty")]
        return {"sources": sources, "answered": not silent, "asked": sorted(parts), "silent": silent}

    def _artefact(self, user_id: str, mark: dict[str, Any]) -> dict[str, Any]:
        for e in mark.get("exposures") or []:  # the exposures the artefact lives in: a bad-picture flag from this person
            self.people.flag(user_id, e, "broken", "mark", ra=mark["ra"], dec=mark["dec"], field_key=mark["field_key"])
        here = self._artefact_marks_near(mark["ra"], mark["dec"])
        who = {m["user_id"] for m in here}
        if len(who) < ARTEFACT_AGREE:
            return {"effect": "artefact", "state": "waiting", "people": len(who)}
        looks = Counter(m["look"] for m in here if m.get("look"))
        look = looks.most_common(1)[0][0] if looks else None
        aside = []
        c = self.db._conn()
        for f in self._finds_near(mark["ra"], mark["dec"]):
            c.execute(
                "INSERT OR REPLACE INTO acc_people_actions(finding_id,action,reason,data,at) VALUES(?,?,?,?,?)",
                (f["finding_id"], "set_aside", "artefact", json.dumps({"look": look, "agree": len(who), "total": len(who)}), time.time()),
            )
            aside.append(f["finding_id"])
        return {"effect": "artefact", "state": "set_aside" if aside else "agreed", "people": len(who), "set_aside": aside}

    def _artefact_marks_near(self, ra: float, dec: float) -> list[dict[str, Any]]:
        d = SAME_PLACE_ARCSEC / 3600.0
        rows = (
            self.db._conn().execute("SELECT * FROM acc_marks WHERE kind='artefact' AND dec BETWEEN ? AND ?", (dec - d, dec + d)).fetchall()
        )
        return [dict(r) for r in rows if sep(ra, dec, r["ra"], r["dec"]) <= SAME_PLACE_ARCSEC]

    def _finds_near(self, ra: float, dec: float) -> list[dict[str, Any]]:
        try:
            from service.routes_tasks import find_rows

            rows = find_rows(self.st)
        except Exception:  # no findings here: nothing of the machine's to set aside
            return []
        return [r for r in rows if r.get("ra") is not None and sep(ra, dec, float(r["ra"]), float(r["dec"])) <= SAME_PLACE_ARCSEC]

    def _odd(self, user_id: str, mark: dict[str, Any]) -> dict[str, Any]:
        note = str(mark.get("note") or "").strip()
        if not note:
            return {"effect": "odd", "state": "no_note"}
        near = self._finds_near(mark["ra"], mark["dec"])
        thread = near[0]["finding_id"] if near else f"place:{mark['ra']:.4f},{mark['dec']:+.4f}"
        with contextlib.suppress(people.AnswerError):
            got = self.people.add_comment(user_id, thread, note, gap=False)
            return {"effect": "odd", "state": "talk", "thread": thread, "comment_id": got["id"]}
        return {"effect": "odd", "state": "refused", "thread": thread}

    # ------------------------------------------------------------------------------------------------------- the shared layer

    def field(self, key: str, viewer: str | None) -> dict[str, Any]:
        """Every person's marks on a field, merged by place and kind, with counts and never a name; and the people's answers on the
        machine finds there. What was said at a place is given only to a person who has marked or answered there."""
        c = self.db._conn()
        box = field_box(key)
        if box is not None:
            _, dec0, size = box
            rows = c.execute("SELECT * FROM acc_marks WHERE dec BETWEEN ? AND ?", (dec0 - size, dec0 + size)).fetchall()
            marks = [dict(r) for r in rows if in_box(box, r["ra"], r["dec"])]
        else:
            marks = [dict(r) for r in c.execute("SELECT * FROM acc_marks WHERE field_key=?", (key,))]
        mine = [m for m in marks if m["user_id"] == viewer]
        places = []
        for g in clusters(marks, SAME_PLACE_ARCSEC):
            ra = sum(m["ra"] for m in g) / len(g)
            dec = sum(m["dec"] for m in g) / len(g)
            seen = bool(viewer) and any(sep(ra, dec, m["ra"], m["dec"]) <= SAME_PLACE_ARCSEC for m in mine)
            who = {m["user_id"] for m in g}
            place: dict[str, Any] = {
                "ra": round(ra, 6),
                "dec": round(dec, 6),
                "people": len(who),
                "mine": bool(viewer) and viewer in who,
                "seen": seen,
            }
            if seen:
                kinds: Counter[str] = Counter()
                for k in {(m["user_id"], m.get("kind") or "mark") for m in g}:
                    kinds[k[1]] += 1
                looks = Counter(m["look"] for m in g if m.get("look"))
                place["kinds"] = [{"kind": k, "label": KIND_LABEL.get(k, "a mark"), "people": n} for k, n in kinds.most_common()]
                place["looks"] = [{"look": k, "label": LOOKS.get(k, k), "people": n} for k, n in looks.most_common()]
                place["passes"] = sorted({m["pass"] for m in g if m.get("pass") is not None})
                top = place["kinds"][0]
                place["text"] = f"{top['people']} {'person' if top['people'] == 1 else 'people'}: {top['label']}" + (
                    f" · {place['looks'][0]['label']}" if top["kind"] == "artefact" and place["looks"] else ""
                )
            places.append(place)
        finds = []
        rows = []
        with contextlib.suppress(Exception):
            from service.routes_tasks import find_rows

            rows = find_rows(self.st)
        here = [
            r
            for r in rows
            if r.get("ra") is not None and (in_box(box, float(r["ra"]), float(r["dec"])) if box else r.get("field_key") == key)
        ]
        ids = [r["finding_id"] for r in here]
        said = self.people.results(ids)
        aside = {r["finding_id"]: json.loads(r["data"]) for r in c.execute(
            f"SELECT finding_id, data FROM acc_people_actions WHERE action='set_aside' AND finding_id IN ({','.join('?' * len(ids)) or "''"})", ids)}  # fmt: skip
        answered = set()
        if viewer:
            answered = {r[0] for r in c.execute("SELECT finding_id FROM acc_series_answers WHERE user_id=?", (viewer,))}
            answered |= {r[0] for r in c.execute("SELECT finding_id FROM acc_answers WHERE user_id=?", (viewer,))}
        for r in here:
            fid = r["finding_id"]
            s = said.get(fid)
            if fid in aside:
                a = aside[fid]
                s = {"result": "artefact", "text": f"people say: artefact, {a.get('agree', 2)} of {a.get('total', 2)}", "agree": a.get("agree"),
                     "total": a.get("total"), "set_aside": True}  # fmt: skip
            if s is None:
                continue
            near_mine = any(sep(float(r["ra"]), float(r["dec"]), m["ra"], m["dec"]) <= SAME_PLACE_ARCSEC for m in mine)
            if fid in answered or near_mine:
                finds.append(
                    {
                        "finding_id": fid,
                        "ra": r["ra"],
                        "dec": r["dec"],
                        "seen": True,
                        **{k: s.get(k) for k in ("result", "text", "agree", "total", "set_aside")},
                    }
                )
            else:
                # not answered here yet: no words that would lead the person, but a find people set aside is said so (it leaves the list)
                finds.append(
                    {
                        "finding_id": fid,
                        "ra": r["ra"],
                        "dec": r["dec"],
                        "seen": False,
                        "answers": s.get("total"),
                        **({"set_aside": True} if s.get("set_aside") else {}),
                    }
                )
        return {"field_key": key, "places": places, "finds": finds}

    # ----------------------------------------------------------------------------------------------------------- the counts

    def counts(self) -> dict[str, Any]:
        c = self.db._conn()
        kinds = {r[0] or "none": int(r[1]) for r in c.execute("SELECT kind, COUNT(*) FROM acc_marks GROUP BY kind")}
        looks = {
            r[0]: int(r[1])
            for r in c.execute("SELECT look, COUNT(*) FROM acc_marks WHERE kind='artefact' AND look IS NOT NULL GROUP BY look")
        }
        jobs = {f"{r[0]}:{r[1]}": int(r[2]) for r in c.execute("SELECT kind, state, COUNT(*) FROM acc_people_jobs GROUP BY kind, state")}
        aside = c.execute("SELECT COUNT(*) FROM acc_people_actions WHERE action='set_aside'").fetchone()[0]
        moves = {s: n for k, n in jobs.items() for kk, s in [k.split(":", 1)] if kk == "moves"}
        found = c.execute(
            "SELECT COUNT(*) FROM acc_people_jobs j JOIN acc_investigations i ON i.id=j.investigation_id WHERE j.kind='moves' AND i.submitted IS NOT NULL"
        ).fetchone()[0]
        lookups = {s: n for k, n in jobs.items() for kk, s in [k.split(":", 1)] if kk == "lookup"}
        return {
            "marks": kinds,
            "artefact_looks": looks,
            "set_aside": int(aside),
            "moves": {**moves, "found": int(found)},
            "lookups": lookups,
        }


def get(st: Any, db: AccountsDB) -> FieldPeople:
    f: FieldPeople | None = getattr(st, "field_people", None)
    if f is None or f.db is not db:
        f = st.field_people = FieldPeople(st, db)
    return f


def set_aside(st: Any) -> dict[str, dict[str, Any]]:
    """Read-only, for the search, Findings and Candidates: machine finds people set aside as artefacts, {finding_id: {look, agree,
    total}}; leave them out of the Candidates list and the default Findings view."""
    from service import accounts

    db = accounts.get(st).db
    get(st, db)
    rows = db._conn().execute("SELECT finding_id, data FROM acc_people_actions WHERE action='set_aside'")
    return {r["finding_id"]: json.loads(r["data"]) for r in rows}


def people_finds(st: Any) -> list[dict[str, Any]]:
    """Read-only: the finds people made from their marks: [{kind: moves | unknown, finding_id | None, state, ra, dec, field_key,
    at}] (moves: the checks job and its investigation; unknown: a source no catalogue has). Never a name."""
    from service import accounts

    db = accounts.get(st).db
    get(st, db)
    # the checks of a moving object and the lookups that can make a find (a single mark's JPL question makes none)
    rows = db._conn().execute("SELECT * FROM acc_people_jobs WHERE kind IN ('moves', 'lookup') ORDER BY at")
    out = []
    for r in rows:
        finding = r["finding_id"]
        if r["kind"] == "moves" and r["investigation_id"]:
            sub = db._conn().execute("SELECT submitted FROM acc_investigations WHERE id=?", (r["investigation_id"],)).fetchone()
            finding = sub["submitted"] if sub else None
        out.append({"kind": "moves" if r["kind"] == "moves" else "unknown", "finding_id": finding, "state": r["state"], "ra": r["ra"], "dec": r["dec"],
                    "field_key": r["field_key"], "at": iso(r["at"])})  # fmt: skip
    return out
