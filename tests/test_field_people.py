"""People's marks are real input, shared on the field (product spec §5g): each kind of mark acts, and the field layer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import accounts
from service.accounts import field_people, people, register_accounts
from service.config import Config

KEY = "40.0000,+10.0000,20"
FINDS: list[dict[str, Any]] = [
    {"finding_id": "mover:9", "kind": "mover", "status": "unexplained", "stage": None, "ra": 40.001, "dec": 10.001, "field_key": "s1"},
    {"finding_id": "red:9", "kind": "red", "status": "unexplained", "stage": None, "ra": 40.02, "dec": 10.02, "field_key": "s1"},
]


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any]:
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    monkeypatch.setenv("SKY_AUTH_DEV", "1")
    monkeypatch.setattr(people, "COMMENT_GAP_S", 0.0)
    st: Any = SimpleNamespace(cfg=Config(store_dir=tmp_path), salt="s", started=[], jobs={}, catalogues={})
    st.task_findings = lambda: FINDS
    st.score_findings = lambda: {f["finding_id"]: f for f in FINDS}

    def start(request: dict[str, Any]) -> str:  # the checks stream's contract: POST /api/checks → {job}
        st.started.append(request)
        job = f"j{len(st.started)}"
        st.jobs[job] = {"job": job, "state": "running", "checks": [{"id": "gaia", "state": "running", "detail": ""}], "can_submit": False}
        return job

    async def lookup(ra: float, dec: float) -> dict[str, Any]:  # every catalogue at the place
        return st.catalogues.get(
            (round(ra, 4), round(dec, 4)), {"sources": [], "answered": True, "asked": ["gaia", "simbad", "deep:catwise"], "silent": []}
        )

    st.bodies = []  # JPL's bodies (name, ra, dec, V, rate ra, rate dec) asked about a single dated "it moves" mark
    st.jpl_state = "ok"

    def jpl_at(ra: float, dec: float, mjd: float) -> dict[str, Any]:
        return {"state": st.jpl_state, "rows": st.bodies} if st.jpl_state == "ok" else {"state": st.jpl_state, "error": "down"}

    st.jpl_at = jpl_at
    st.start_checks = start
    st.checks_status = lambda job: st.jobs.get(job)
    st.lookup_place = lookup
    app = FastAPI()
    register_accounts(app, st)
    return app, st


def _people(app: FastAPI) -> Any:
    made: dict[str, TestClient] = {}

    def user(name: str) -> TestClient:
        if name not in made:
            c = TestClient(
                app, base_url="http://localhost", follow_redirects=False, headers={"cf-connecting-ip": f"10.0.1.{len(made) + 1}"}
            )
            c.get(c.post("/api/auth/email", json={"email": f"{name}@example.org"}).json()["dev_link"].replace("http://localhost", ""))
            c.patch("/api/me", json={"name": name.title()})
            made[name] = c
        return made[name]

    return user


def _mark(c: TestClient, mid: str, kind: str, ra: float, dec: float, **kw: Any) -> dict[str, Any]:
    body = {"id": mid, "field_key": KEY, "ra": ra, "dec": dec, "kind": kind, **kw}
    r = c.post("/api/me/marks", json=body)
    assert r.status_code == 200, r.text
    got: dict[str, Any] = r.json()
    return got


def test_it_moves_on_two_passes_starts_the_checks_and_a_finding_found_by_people(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    ann = _people(app)("ann")
    one = _mark(ann, "mv-0000001", "moves", 40.1, 10.1, **{"pass": 0, "mjd": 61100.1})
    assert one["effect"]["state"] == "needs_another_pass" and st.started == []
    two = _mark(ann, "mv-0000002", "moves", 40.1003, 10.1001, **{"pass": 1, "mjd": 61280.4})
    assert two["effect"]["state"] == "checking" and len(st.started) == 1
    req = st.started[0]
    assert req["field_key"] == KEY and [m["pass"] for m in req["marks"]] == [0, 1] and req["marks"][1]["mjd"] == 61280.4
    inv = ann.get("/api/me/investigations").json()["investigations"][0]
    assert (
        inv["title"].startswith("It moves: RA 40.1000° Dec +10.1000° (found by people)")
        and inv["state"] == "checking"
        and inv["mark_ids"] == ["mv-0000001", "mv-0000002"]
    )
    assert (
        _mark(ann, "mv-0000003", "moves", 40.1006, 10.1002, **{"pass": 2})["effect"]["state"] == "checking" and len(st.started) == 1
    )  # one job per object
    # another body in the same 10′ field (100″ away, on the first pass): its own object, not "checking" already
    other = _mark(ann, "mv-0000009", "moves", 40.1 + 100 / 3600, 10.1, **{"pass": 0, "mjd": 61100.1})
    assert other["effect"]["state"] == "needs_another_pass" and len(st.started) == 1
    # the checks answer, none failed: submitted by itself, under her name
    st.jobs["j1"] = {"job": "j1", "state": "done", "checks": [{"id": "gaia", "state": "pass", "detail": ""}], "can_submit": True}
    st.on_checks_done(st.jobs["j1"])
    mine = ann.get("/api/me/findings").json()["findings"]
    assert len(mine) == 1 and mine[0]["role"] == "submitted" and "found by people" in mine[0]["title"]
    assert [x["finding_id"] for x in field_people.people_finds(st)] == [mine[0]["finding_id"]]
    # another person: two visits of one pass count too; a failing check is said with its reason
    bob = _people(app)("bob")
    _mark(bob, "mv-0000011", "moves", 40.2, 10.2, **{"pass": 0, "mjd": 61100.10})
    assert _mark(bob, "mv-0000012", "moves", 40.2002, 10.2, **{"pass": 0, "mjd": 61100.35})["effect"]["state"] == "checking"
    st.jobs["j2"] = {
        "job": "j2",
        "state": "done",
        "checks": [{"id": "gaia", "state": "fail", "detail": "Gaia DR3 7 stands 1″ from every mark."}],
        "can_submit": False,
    }
    st.on_checks_done(st.jobs["j2"])
    told = [n["text"] for n in bob.get("/api/me/notifications").json()["notifications"] if n["kind"] == "checks"]
    assert told and "was not submitted. Gaia: Gaia DR3 7 stands 1″ from every mark." in told[0]
    # marks far apart are two objects
    _mark(bob, "mv-0000021", "moves", 40.5, 10.5, **{"pass": 0})
    assert _mark(bob, "mv-0000022", "moves", 40.0, 10.9, **{"pass": 1})["effect"]["state"] == "needs_another_pass"


def test_not_in_the_catalogues_asks_every_catalogue_and_makes_an_unknown_source(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    user = _people(app)
    ann = user("ann")
    st.catalogues[(40.3, 10.3)] = {
        "sources": [{"ra": 40.3, "dec": 10.3002, "name": "Gaia DR3 123"}],
        "answered": True,
        "asked": ["gaia"],
        "silent": [],
    }
    known = _mark(ann, "nc-0000001", "not_in_catalogues", 40.3, 10.3)["effect"]
    assert known == {"effect": "not_in_catalogues", "state": "known", "matches": ["Gaia DR3 123"]}
    st.catalogues[(40.31, 10.31)] = {"sources": [], "answered": False, "asked": ["gaia", "deep:catwise"], "silent": ["deep:catwise"]}
    assert _mark(ann, "nc-0000002", "not_in_catalogues", 40.31, 10.31)["effect"]["state"] == "no_answer"
    new = _mark(ann, "nc-0000003", "not_in_catalogues", 40.32, 10.32)["effect"]
    assert new["state"] == "unknown" and new["finding_id"].startswith("user:")
    sub = next(f for f in ann.get("/api/me/findings").json()["findings"] if f["finding_id"] == new["finding_id"])
    assert sub["title"].startswith("Unknown source, found by people") and sub["status"] == "submitted"
    queue = accounts.get(st).db._conn().execute("SELECT finding_id FROM acc_bd_queue").fetchall()
    assert [r[0] for r in queue] == [new["finding_id"]]  # its spectrum goes to the brown-dwarf search's check
    credit = accounts.get(st).db.credits_of([new["finding_id"]])[new["finding_id"]]
    assert {(x["name"], x["role"]) for x in credit} == {("Ann", "submitted"), ("Ann", "marked")}


def test_artefact_marks_of_two_people_set_the_machine_find_aside(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    user = _people(app)
    ann, bob = user("ann"), user("bob")
    exp = "dr1/level2/2025W33_2C/l2b-v27-2026-228/3/level2_2025W33_2C_0321_1D3_spx_l2b-v27-2026-228.fits"
    a = _mark(ann, "af-0000001", "artefact", 40.001, 10.001, look="glare", exposures=[exp])["effect"]
    assert a == {"effect": "artefact", "state": "waiting", "people": 1}
    assert _mark(ann, "af-0000002", "artefact", 40.0011, 10.001, look="spike")["effect"]["people"] == 1  # the same person again
    b = _mark(bob, "af-0000011", "artefact", 40.0012, 10.0011, look="glare", exposures=[exp])["effect"]
    assert b["state"] == "set_aside" and b["set_aside"] == ["mover:9"] and b["people"] == 2
    assert field_people.set_aside(st) == {"mover:9": {"look": "glare", "agree": 2, "total": 2}}
    res = ann.get("/api/tasks/results", params={"ids": "mover:9"}).json()["results"]["mover:9"]
    assert res["text"] == "people say: artefact, 2 of 2" and res["set_aside"] is True
    assert people.bad_exposures(st) == {exp}  # the exposure it lives in: flagged by two people
    assert (
        ann.post(
            "/api/me/marks", json={"id": "af-0000003", "field_key": KEY, "ra": 40.0, "dec": 10.0, "kind": "moves", "look": "glare"}
        ).status_code
        == 422
    )


def test_something_odd_goes_to_the_talk_of_the_place(site: tuple[FastAPI, Any]) -> None:
    app, _st = site
    ann = _people(app)("ann")
    r = ann.post("/api/me/marks", json={"id": "od-0000001", "field_key": KEY, "ra": 40.02, "dec": 10.02, "kind": "odd"})
    assert r.status_code == 422 and "say in a few words" in r.json()["detail"]
    near = _mark(ann, "od-0000002", "odd", 40.0201, 10.0201, note="A faint arc next to it, in every pass")["effect"]
    assert near["state"] == "talk" and near["thread"] == "red:9"  # the machine find at that place
    away = _mark(ann, "od-0000003", "odd", 40.5, 10.5, note="A green blob")["effect"]
    assert away["thread"] == "place:40.5000,+10.5000"
    talk = TestClient(app).get("/api/findings/red:9/comments").json()["comments"]
    assert [(x["name"], x["text"]) for x in talk] == [("Ann", "A faint arc next to it, in every pass")]


def test_the_field_layer_merges_by_place_and_kind_and_shows_what_was_said_only_after_your_own_answer(site: tuple[FastAPI, Any]) -> None:
    app = site[0]
    user = _people(app)
    ann, bob, cid = user("ann"), user("bob"), user("cid")
    _mark(ann, "fl-0000001", "artefact", 40.05, 10.05, look="blinks")
    _mark(bob, "fl-0000011", "artefact", 40.05001, 10.05001, look="blinks")
    _mark(bob, "fl-0000012", "moves", 40.06, 10.06, **{"pass": 0})
    guest = TestClient(app).get(f"/api/field/{KEY}/people").json()
    assert sorted((p["people"], p["seen"]) for p in guest["places"]) == [(1, False), (2, False)]
    assert all("kinds" not in p for p in guest["places"])  # a guest sees that people marked, not what they said
    for_cid = cid.get(f"/api/field/{KEY}/people").json()
    assert all(not p["seen"] for p in for_cid["places"])
    _mark(cid, "fl-0000021", "artefact", 40.05002, 10.05, look="spike")  # cid marks there herself: now she sees what was said
    place = next(p for p in cid.get(f"/api/field/{KEY}/people").json()["places"] if p["people"] == 3)
    assert (
        place["seen"]
        and place["text"] == "3 people: artefact · blinks on and off"
        and place["looks"][0] == {"look": "blinks", "label": "blinks on and off", "people": 2}
    )
    dump = str(cid.get(f"/api/field/{KEY}/people").json())
    assert "Ann" not in dump and "example.org" not in dump and "user_id" not in dump  # never a name
    # a machine find people answered: the answer is shown only to those who answered it themselves
    for name in ("dee", "eve", "fay"):
        c = user(name)
        t = c.get("/api/tasks/next", params={"kind": "moves", "finding_id": "mover:9"}).json()["task"]
        c.post(f"/api/me/tasks/{t['id']}/done", json={"path": ["yes", "one", "no"]})
    finds = {f["finding_id"]: f for f in user("dee").get(f"/api/field/{KEY}/people").json()["finds"]}
    assert finds["mover:9"]["seen"] and finds["mover:9"]["text"] == "people say: it shifts, 3 of 3"
    hidden = {f["finding_id"]: f for f in ann.get(f"/api/field/{KEY}/people").json()["finds"]}
    assert hidden["mover:9"] == {"finding_id": "mover:9", "ra": 40.001, "dec": 10.001, "seen": False, "answers": 3}
    # people set it aside: one who has not answered sees that (so it leaves the list), still without the words
    from service import accounts as acc_mod

    acc_mod.get(site[1]).db._conn().execute(
        "INSERT INTO acc_people_actions(finding_id, action, reason, data, at) VALUES(?,?,?,?,?)",
        ("mover:9", "set_aside", "artefact", '{"agree": 2, "total": 2}', 1.0),
    )
    hidden = {f["finding_id"]: f for f in ann.get(f"/api/field/{KEY}/people").json()["finds"]}
    assert hidden["mover:9"]["set_aside"] is True and hidden["mover:9"]["seen"] is False and "text" not in hidden["mover:9"]
    counts = ann.get("/api/people/counts").json()["field"]
    assert counts["marks"]["artefact"] == 3 and counts["artefact_looks"] == {"blinks": 2, "spike": 1} and counts["marks"]["moves"] == 1


def test_mover_marks_link_only_across_passes_and_within_reach() -> None:
    """Marks on the same picture are two objects; between passes months apart a body travels at most ~120″/yr, between visits
    hours apart up to ~150″/h (field_people.mover_linked)."""
    from service.accounts.field_people import mover_groups, mover_linked

    d = 1 / 3600
    a = {"ra": 40.0, "dec": 10.0, "pass": 0, "mjd": 61100.0}
    assert not mover_linked(a, {**a, "ra": 40.0 + 5 * d})  # the same pass and visit: another object
    assert mover_linked(a, {"ra": 40.0 + 40 * d, "dec": 10.0, "pass": 1, "mjd": 61280.0})  # half a year, 40″: a slow mover
    assert not mover_linked(a, {"ra": 40.0 + 300 * d, "dec": 10.0, "pass": 1, "mjd": 61280.0})  # 300″ in half a year: another
    assert mover_linked(a, {"ra": 40.0 + 300 * d, "dec": 10.0, "pass": 0, "mjd": 61100.1})  # 300″ in 2.4 h: an asteroid's visits
    assert not mover_linked(a, {"ra": 40.0 + 300 * d, "dec": 10.0, "pass": 0, "mjd": 61100.01})  # 300″ in 14 minutes: too fast
    assert mover_linked({**a, "mjd": None}, {"ra": 40.0 + 50 * d, "dec": 10.0, "pass": 1})  # no dates: 60″ between passes
    marks = [
        a,
        {"ra": 40.0 + 40 * d, "dec": 10.0, "pass": 1, "mjd": 61280.0},
        {"ra": 40.0 + 200 * d, "dec": 10.0, "pass": 0, "mjd": 61100.0},
    ]
    assert sorted(len(g) for g in mover_groups(marks)) == [1, 2]


def test_a_marks_link_is_kept_and_returned(site: tuple[FastAPI, Any]) -> None:
    """The lab says a second "it moves" tap is the same mover as an earlier mark (`link`): kept and given back after a reload."""
    app, _st = site
    ann = _people(app)("ann")
    _mark(ann, "mv-0000101", "moves", 40.1, 10.1, **{"pass": 0, "mjd": 61100.1})
    _mark(ann, "mv-0000102", "moves", 40.1003, 10.1001, **{"pass": 1, "mjd": 61280.4, "link": "mv-0000101"})
    got = {m["id"]: m.get("link") for m in ann.get("/api/me/marks").json()["marks"]}
    assert got["mv-0000102"] == "mv-0000101" and got["mv-0000101"] is None
    assert (
        ann.post("/api/me/marks", json={"id": "mv-0000103", "field_key": KEY, "ra": 1.0, "dec": 1.0, "link": "bad id!"}).status_code == 422
    )


def test_every_mark_ends_with_a_verdict(site: tuple[FastAPI, Any]) -> None:
    """Explorer round 3 B-1: a mark's Checks step never finished, and a known asteroid was never named. One dated "it moves" mark asks
    JPL at once: a known body there at that moment is named; JPL not answering says so with the time it is asked again, and the
    service's loop asks again until it answers; the marks list carries every mark's verdict."""
    import asyncio

    app, st = site
    ann = _people(app)("ann")
    # a known asteroid where the mark is, at that moment
    st.bodies = [["12304 (1991 SR1)", 60.89821, 8.58951, 19.4, 30.0, -10.0]]
    got = _mark(ann, "mv-0000101", "moves", 60.8982, 8.5895, **{"mjd": 61085.548})
    assert got["effect"]["state"] == "known" and got["effect"]["name"] == "12304 (1991 SR1)"
    # JPL down: the mark says it could not be checked yet and when it is asked again
    st.bodies, st.jpl_state = [], "unavailable"
    got = _mark(ann, "mv-0000102", "moves", 61.5, 9.5, **{"mjd": 61085.955})
    assert got["effect"]["state"] == "jpl_wait" and got["effect"]["retry_at"]
    marks = {m["id"]: m for m in ann.get("/api/me/marks").json()["marks"]}
    assert marks["mv-0000101"]["verdict"]["state"] == "known" and "12304" in marks["mv-0000101"]["verdict"]["text"]
    v = marks["mv-0000102"]["verdict"]
    assert v["state"] == "waiting" and "JPL did not answer" in v["text"] and "asked again at" in v["text"]
    # JPL answers on the retry (its time has come): no body there, so it needs a second mark; the verdict follows
    fp = field_people.get(st, accounts.get(st).db)
    fp.db._conn().execute("UPDATE acc_people_jobs SET data=json_set(data, '$.retry_at', 0) WHERE id='single:mv-0000102'")
    st.jpl_state = "ok"
    assert asyncio.run(fp.retry_due()) == 1
    v = {m["id"]: m for m in ann.get("/api/me/marks").json()["marks"]}["mv-0000102"]["verdict"]
    assert v["state"] == "needs_another_pass" and "no known body" in v["text"]


def test_a_mark_made_before_the_single_check_gets_its_answer(site: tuple[FastAPI, Any]) -> None:
    """The explorer's two marks (one dated mark each) never got a check: at start they are queued and asked."""
    import asyncio

    app, st = site
    ann = _people(app)("ann")
    _mark(ann, "mv-0000201", "moves", 60.8982, 8.5895, **{"mjd": 61085.548})
    fp = field_people.get(st, accounts.get(st).db)
    fp.db._conn().execute("DELETE FROM acc_people_jobs")  # as before the single check existed
    assert fp.backfill() == 1 and fp.backfill() == 0  # queued once
    st.bodies = [["12304 (1991 SR1)", 60.89821, 8.58951, 19.4, 30.0, -10.0]]
    assert asyncio.run(fp.retry_due()) == 1
    v = {m["id"]: m for m in ann.get("/api/me/marks").json()["marks"]}["mv-0000201"]["verdict"]
    assert v["state"] == "known" and v["name"] == "12304 (1991 SR1)"


def test_jpl_still_being_asked_is_checking_not_a_failure(site: tuple[FastAPI, Any]) -> None:
    """JPL's answer takes a minute or two: "pending" is not "did not answer"; the mark says it is asking and looks again in a minute."""
    app, st = site
    ann = _people(app)("ann")
    st.jpl_state = "pending"
    got = _mark(ann, "mv-0000301", "moves", 62.0, 9.0, **{"mjd": 61085.6})
    assert got["effect"]["state"] == "checking"
    v = {m["id"]: m for m in ann.get("/api/me/marks").json()["marks"]}["mv-0000301"]["verdict"]
    assert v["state"] == "checking" and "Asking JPL" in v["text"]
    db = field_people.get(st, accounts.get(st).db).db
    row = db._conn().execute("SELECT data FROM acc_people_jobs WHERE id='single:mv-0000301'").fetchone()
    assert '"tries": 0' in row[0]  # no try spent
