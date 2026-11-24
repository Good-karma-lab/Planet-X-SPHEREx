# ruff: noqa: E501
"""What people do for the machine (product spec §5e): the question series, what each answer does, the other task kinds, talk,
control objects and weights, the counts for the Method page."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import accounts
from service.accounts import people, register_accounts, score
from service.accounts.db import AccountsDB
from service.accounts.people import AGREE_WEIGHT, BAD_FLAGS, MAX_ANSWERS, AnswerError, People, run_path, steps_of
from service.config import Config

VISITS = [{"ra": 10.0 + i * 1e-3, "dec": 5.0, "mjd": 61100.0 + i} for i in range(5)]
FINDS: list[dict[str, Any]] = [
    {"finding_id": "film:1", "kind": "film", "status": "unexplained", "stage": None, "ra": 10.0, "dec": 5.0, "field_key": "k1", "data": {"visits": VISITS}},
    {"finding_id": "film:2", "kind": "film", "status": "unexplained", "stage": None, "ra": 11.0, "dec": 5.0, "field_key": "k1", "data": {"visits": VISITS}},
    {"finding_id": "mover:1", "kind": "mover", "status": "unchecked", "stage": None, "ra": 12.0, "dec": 5.0, "field_key": "k2"},
    {"finding_id": "peak:1", "kind": "peak", "status": "unchecked", "stage": None, "ra": 13.0, "dec": 5.0, "field_key": "k3"},
    {"finding_id": "red:1", "kind": "red", "status": "unexplained", "stage": None, "ra": 14.0, "dec": 5.0, "field_key": "k4"},
    {"finding_id": "anomaly:1", "kind": "anomaly", "status": "unexplained", "stage": None, "ra": 15.0, "dec": 5.0, "field_key": "k5"},
    {"finding_id": "film:known", "kind": "film", "status": "known", "stage": "known", "ra": 16.0, "dec": 5.0, "field_key": "k6", "data": {"visits": VISITS}},
]  # fmt: skip
ROW = {f["finding_id"]: f for f in FINDS}


@pytest.fixture
def pp(tmp_path: Path) -> People:
    st = SimpleNamespace(finds={})
    st.score_findings = lambda: st.finds
    return People(score.Score(st, AccountsDB(tmp_path / "product.sqlite")))


def _users(pp: People, n: int, start: int = 0) -> list[str]:
    return [pp.db.user_for_email(f"u{i}@example.org")["id"] for i in range(start, start + n)]


def _score(pp: People, uid: str) -> tuple[int, float | None]:
    s = pp.score.summary(uid)
    return s["points"], s["accuracy"]


# ----------------------------------------------------------------------------------------------------------------- the series


def test_the_series_are_the_spec_wording_and_never_ask_what_the_machine_knows() -> None:
    first = {s: steps_of(s)[0] for s in people.SERIES}
    assert first["track"]["question"].startswith("Left: the pictures added up along its path.")
    assert steps_of("track")[1]["question"] == "Does one faint dot move along the arrow?"
    assert [a["label"] for a in steps_of("track")[1]["answers"]] == ["Yes, in every picture", "Only in some", "No", "Not sure"]
    assert steps_of("track")[2]["question"] == "Tap the pictures where it is missing." and steps_of("track")[2]["tap"] is True
    assert [a["label"] for a in steps_of("track")[3]["answers"]] == ["Dot", "Streak", "Not sure"]
    assert first["slow"]["question"] == "Does the dot in the ring move while the stars stay still?"
    assert steps_of("slow")[1]["question"] == "Is it one dot, or two touching?"
    assert steps_of("slow")[2]["question"] == "Old pictures (WISE 2010, 2MASS 2000): is there a dot along the arrow, further back?"
    assert first["peak"]["question"].startswith("Left: all the pictures added up along its possible path.")
    assert steps_of("peak")[1]["question"] == "Is it inside a bright star's glow, or on one of its rays?"
    assert [s["question"] for s in steps_of("red")] == [
        "A sharp dot, or a fuzzy patch?",
        "Is it there every time?",
        "Is it on a bright star's glow or ray, or at the picture's edge?",
        "Old picture next to new: has it moved?",
    ]
    assert [s["question"] for s in steps_of("anomaly")] == [
        "Is another star touching the one in the middle?",
        "Do these pictures look damaged (a streak, cut off, glare)?",
    ]
    every = [a["label"] for s in people.SERIES for step in steps_of(s) for a in step["answers"]] + [
        s["question"] for x in people.SERIES for s in steps_of(x)
    ]
    for word in ("class", "brown dwarf", "dusty", "galaxy", "detector", "exposure", "catalogue", "Gaia", "SIMBAD", "Known object"):
        assert not any(word.lower() in w.lower() for w in every), word  # spec §5e: "Not asked of people"
    assert all(
        step["answers"][-1] == {"id": "not_sure", "label": "Not sure", "result": "cant_tell"} for s in people.SERIES for step in steps_of(s)
    )


def test_paths_lead_to_results() -> None:
    cases = {
        "track": {("every", "dot"): "moves", ("some", "done", "dot"): "partial", ("no",): "no_dot", ("every", "streak"): "streak",
                  ("some", "done", "streak"): "streak", ("not_sure",): "cant_tell", ("every", "not_sure"): "cant_tell"},
        "slow": {("no",): "still", ("yes", "two"): "blend", ("yes", "one", "yes"): "moves_old", ("yes", "one", "no"): "moves"},
        "peak": {("no",): "no_peak", ("yes", "yes"): "glow", ("yes", "no"): "candidate"},
        "red": {("fuzzy",): "galaxy", ("sharp", "no"): "artefact", ("sharp", "yes", "yes"): "artefact", ("sharp", "yes", "no", "yes"): "brown_dwarf",
                ("sharp", "yes", "no", "no"): "still"},
        "anomaly": {("yes",): "neighbour", ("no", "yes"): "broken", ("no", "no"): "real"},
    }  # fmt: skip
    for series, paths in cases.items():
        for path, want in paths.items():
            assert run_path(series, list(path), [1]) == want, (series, path)
        assert set(paths.values()) - {"cant_tell"} == set(people.RESULTS[series]), series
    # "Only in some", then Done with none tapped: missing from none of them, so in every picture (M-1: it was a 422)
    assert run_path("track", ["some", "done", "dot"], []) == "moves"
    assert run_path("track", ["some", "done", "dot"], None) == "moves"
    for bad in ([], ["every"], ["no", "dot"], ["maybe"], ["not_sure", "dot"]):
        with pytest.raises(AnswerError):
            run_path("track", bad, [1])


# -------------------------------------------------------------------------------------------------------- agreement and actions


def test_three_people_settle_and_the_answer_acts(pp: People) -> None:
    a, b, c, d = _users(pp, 4)
    assert pp.answer(a, "track", ROW["film:1"], ["no"], [], []) == ("no_dot", 0)
    assert pp.answer(b, "track", ROW["film:1"], ["every", "dot"], [], [])[1] == 0
    assert pp.answer(c, "track", ROW["film:1"], ["not_sure"], [], [])[1] == 0  # neither right nor wrong, never paid
    assert pp.results(["film:1"]) == {}
    assert pp.answer(d, "track", ROW["film:1"], ["no"], [], []) == ("no_dot", 0)
    assert pp.results(["film:1"]) == {}  # 2.0 of weight agree, 4 answers: not yet
    e = _users(pp, 1, 4)[0]
    assert pp.answer(e, "track", ROW["film:1"], ["no"], [], [])[1] == 10  # the third agreeing answer settles it
    said = pp.results(["film:1"])["film:1"]
    assert (said["result"], said["agree"], said["total"], said["text"]) == (
        "no_dot",
        3,
        5,
        "people say: no dot steps along the arrow, 3 of 5",
    )
    assert [_score(pp, x) for x in (a, b, c, d, e)] == [(10, 1.0), (0, 0.0), (0, None), (10, 1.0), (10, 1.0)]
    action = pp.db._conn().execute("SELECT action, reason FROM acc_people_actions WHERE finding_id='film:1'").fetchone()
    assert (action["action"], action["reason"]) == ("drop", "no_dot")
    assert pp.answer(a, "track", ROW["film:1"], ["every", "dot"], [], [])[1] == 0  # the first answer stays


def test_a_partial_track_is_relinked_without_the_pictures_most_people_tapped(pp: People) -> None:
    u = _users(pp, 3)
    for uid, taps in zip(u, ([1, 3], [3], [3, 4]), strict=True):
        pp.answer(uid, "track", ROW["film:2"], ["some", "done", "dot"], taps, [])
    row = pp.db._conn().execute("SELECT action, data FROM acc_people_actions WHERE finding_id='film:2'").fetchone()
    assert row["action"] == "relink" and row["data"] == '{"missing": [3]}'  # 3 was tapped by all; 1 and 4 by one of three


def test_every_series_does_what_the_spec_says(pp: People) -> None:
    def settle(series: str, fid: str, path: list[str], exposures: list[str] | None = None, start: int = 0) -> None:
        for uid in _users(pp, 3, start):
            pp.answer(uid, series, ROW[fid], path, [], exposures or [])

    settle("slow", "mover:1", ["yes", "two"])
    settle("peak", "peak:1", ["yes", "yes"], start=10)
    settle("red", "red:1", ["sharp", "yes", "no", "yes"], start=20)
    settle("anomaly", "anomaly:1", ["yes"], ["exp-A", "exp-B"], start=30)
    acts = {r["finding_id"]: (r["action"], r["reason"], r["data"]) for r in pp.db._conn().execute("SELECT * FROM acc_people_actions")}
    assert acts["mover:1"] == ("drop", "blend", "{}")
    assert acts["peak:1"] == ("drop", "glow", "{}")
    assert acts["red:1"][:2] == ("bd_queue", "brown_dwarf")
    assert acts["anomaly:1"] == ("leave_out", "neighbour", '{"exposures": ["exp-A", "exp-B"]}')
    assert [q["finding_id"] for q in pp.agreement.brown_dwarf_queue()] == ["red:1"]  # sent to the brown-dwarf search
    pp.db._conn().execute("DELETE FROM acc_series_results"), pp.db._conn().execute("DELETE FROM acc_series_answers")
    settle("slow", "mover:1", ["yes", "one", "yes"], start=40)
    settle("peak", "peak:1", ["yes", "no"], start=50)
    acts = {
        r["finding_id"]: (r["action"], r["reason"]) for r in pp.db._conn().execute("SELECT * FROM acc_people_actions WHERE action='raise'")
    }
    assert acts == {"mover:1": ("raise", "moves_old"), "peak:1": ("raise", "candidate")}


def test_broken_pictures_are_bad_after_two_distinct_people(pp: People) -> None:
    a, b = _users(pp, 2)
    pp.answer(a, "anomaly", ROW["anomaly:1"], ["no", "yes"], [], ["exp-1", "exp-2"])  # each "broken" answer flags those exposures
    assert people._bad(pp.db) == set()
    pp.flag(a, "exp-1", "broken", "flag")  # the same person again: still one
    assert people._bad(pp.db) == set()
    assert pp.flag(b, "exp-1", "broken", "bad_picture") == BAD_FLAGS
    assert people._bad(pp.db) == {"exp-1"}
    pp.flag(b, "exp-1", "fine", "bad_picture")  # the last word of a person stays
    assert people._bad(pp.db) == set()


def test_low_accuracy_weighs_less_and_high_accuracy_more(pp: People) -> None:
    good, bad = _users(pp, 2)
    for i in range(6):  # six judged answers each: good always right, bad always wrong
        pp.score._event(good, "check", f"g{i}", 10, True, {})
        pp.score._event(bad, "check", f"b{i}", 0, False, {})
    assert pp.weight_of(good) == 1.5 and pp.weight_of(bad) == 0.5 and pp.weight_of(_users(pp, 1, 5)[0]) == 1.0
    two_good = [good, *_users(pp, 1, 7)]
    pp.score._event(two_good[1], "check", "x", 10, True, {})
    pp.answer(two_good[0], "slow", ROW["mover:1"], ["no"], [], [])  # 1.5
    pp.answer(two_good[1], "slow", ROW["mover:1"], ["no"], [], [])  # 1.0 (only one judged answer)
    assert pp.results(["mover:1"]) == {}  # 2.5 < AGREE_WEIGHT
    pp.answer(bad, "slow", ROW["mover:1"], ["no"], [], [])  # +0.5 = 3.0
    assert pp.results(["mover:1"])["mover:1"]["result"] == "still" and AGREE_WEIGHT == 3.0
    # five answers settle whatever the weights; a tie has no result and nobody is right or wrong
    u = _users(pp, 5, 20)
    for uid, path in zip(u, (["no"], ["yes", "two"], ["no"], ["yes", "two"], ["not_sure"]), strict=True):
        pp.answer(uid, "slow", ROW["mover:1"] | {"finding_id": "mover:tie"}, path, [], [])
    assert pp.results(["mover:tie"])["mover:tie"]["text"] == f"people do not agree ({MAX_ANSWERS} answers)"
    assert [_score(pp, x) for x in u] == [(0, None)] * 5


def test_control_objects_are_judged_at_once_and_mixed_into_the_series(pp: People) -> None:
    a, b = _users(pp, 2)
    assert pp.expected("track", ROW["film:known"]) == frozenset({"moves", "partial"})  # a body JPL knows does step along
    assert pp.answer(a, "track", ROW["film:known"], ["every", "dot"], [], [])[1] == 10
    assert pp.answer(b, "track", ROW["film:known"], ["no"], [], [])[1] == 0
    assert _score(pp, a) == (10, 1.0) and _score(pp, b) == (0, 0.0)
    assert pp.results(["film:known"]) == {}  # a control is not settled by people
    # every fifth task of a person is a control object, when there is one
    c = _users(pp, 1, 5)[0]
    picked = []
    for i in range(5):
        row = pp.pick("moves", FINDS, c, set())
        assert row is not None
        picked.append(row["finding_id"])
        pp.answer(c, str(people.series_of(row["kind"])), row, ["not_sure"], [], [])
        assert i < 4 or row["finding_id"] == "film:known"
    assert picked[:4] == ["film:1", "film:2", "mover:1", "peak:1"] and picked[4] == "film:known"
    # a find people settled unanimously is a control object for the others
    for uid in _users(pp, 3, 10):
        pp.answer(uid, "red", ROW["red:1"], ["fuzzy"], [], [])
    assert pp.expected("red", ROW["red:1"]) == frozenset({"galaxy"})


# ---------------------------------------------------------------------------------------------------- the service: tasks, talk


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any]:
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    monkeypatch.setenv("SKY_AUTH_DEV", "1")
    st: Any = SimpleNamespace(cfg=Config(store_dir=tmp_path), salt="s")
    st.task_findings = lambda: FINDS
    st.score_findings = lambda: {f["finding_id"]: f for f in FINDS}
    app = FastAPI()
    register_accounts(app, st)
    return app, st


def _people(app: FastAPI) -> Any:
    made: dict[str, TestClient] = {}

    def user(name: str) -> TestClient:
        if name not in made:
            # each person from their own address: the sign-in limit is five links per client
            c = TestClient(
                app, base_url="http://localhost", follow_redirects=False, headers={"cf-connecting-ip": f"10.0.0.{len(made) + 1}"}
            )
            c.get(c.post("/api/auth/email", json={"email": f"{name}@example.org"}).json()["dev_link"].replace("http://localhost", ""))
            c.patch("/api/me", json={"name": name.title()})
            made[name] = c
        return made[name]

    return user


def test_series_through_the_api_and_the_read_calls(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    user = _people(app)
    ann = user("ann")
    t = ann.get("/api/tasks/next", params={"kind": "moves", "finding_id": "film:2"}).json()["task"]
    # the page's slug names the same find (track-2 is film:2) and gives the same open task
    assert ann.get("/api/tasks/next", params={"kind": "moves", "finding_id": "track-2"}).json()["task"]["finding_id"] == "film:2"
    assert t["series"]["id"] == "track" and [s["id"] for s in t["series"]["steps"]] == ["sum", "steps", "missing", "shape"]
    assert [p["i"] for p in t["pictures"]] == [0, 1, 2, 3, 4] and t["pictures"][2]["mjd"] == 61102.0
    assert "question" not in t and "answers" not in t and "tree" not in t  # no one-click classes any more
    out = ann.post(f"/api/me/tasks/{t['id']}/done", json={"path": ["some", "done", "dot"], "pictures": [3]}).json()
    assert (
        out["result"] == {"id": "partial", "label": "A dot steps along the arrow, not in every picture"}
        and out["task"]["result"] == "partial"
    )
    # the answer is recorded and says where the agreement stands: points come when people agree
    ag = out["agreement"]
    assert ag["answers"] == 1 and ag["needed"] == 3 and ag["settled"] is False and ag["points_when_agreed"] == 10 and out["points"] == 0
    for name in ("bob", "cid"):
        c = user(name)
        tt = c.get("/api/tasks/next", params={"kind": "moves", "finding_id": "film:2"}).json()["task"]
        c.post(f"/api/me/tasks/{tt['id']}/done", json={"path": ["some", "done", "dot"], "pictures": [3, 4]})
    assert people.missing_visits(st, "film:2") == {3, 4}  # 3 was tapped by all three, 4 by two of three: at least half
    for name, fid, path, exp in (
        ("dee", "anomaly:1", ["yes"], ["e1"]),
        ("eve", "anomaly:1", ["yes"], ["e1", "e2"]),
        ("fay", "anomaly:1", ["yes"], []),
    ):
        c = user(name)
        tt = c.get("/api/tasks/next", params={"kind": "anomaly", "finding_id": fid}).json()["task"]
        assert tt["series"]["id"] == "anomaly"
        c.post(f"/api/me/tasks/{tt['id']}/done", json={"path": path, "exposures": exp})
    assert people.left_out(st, "anomaly:1") == {"e1", "e2"}
    for name in ("gus", "hal", "ivy"):
        c = user(name)
        tt = c.get("/api/tasks/next", params={"kind": "moves", "finding_id": "peak:1"}).json()["task"]
        c.post(f"/api/me/tasks/{tt['id']}/done", json={"path": ["yes", "yes"]})
    assert people.dropped(st) == {"peak:1": "glow"} and people.raised(st) == {}
    res = ann.get("/api/tasks/results", params={"ids": "film:2,peak:1"}).json()["results"]
    assert res["peak:1"]["text"] == "people say: in a bright star's glow or on its spike, 3 of 3"
    # the old one-click answer of before is still taken (red: a class), but not offered any more
    t = user("jo").get("/api/tasks/next", params={"kind": "red", "finding_id": "red:1"}).json()["task"]
    assert t["series"]["id"] == "red" and "answers" not in t
    assert user("jo").post(f"/api/me/tasks/{t['id']}/done", json={"result": "galaxy", "mark_ids": []}).status_code == 200
    counts = ann.get("/api/people/counts").json()
    glow = next(r for r in counts["rules"] if r["said"] == "glow")
    assert (glow["times"], glow["answers"], glow["share"], glow["settled"]) == (3, 3, 1.0, 1)
    assert counts["series"]["track"]["steps"]["steps"] == {"some": 3} and counts["series"]["track"]["steps"]["sum"] == {"yes": 3} and counts["series"]["track"]["results"] == {"partial": 3}


def test_find_again_recentre_bad_picture_second_look(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    user = _people(app)
    ann, bob = user("ann"), user("bob")
    t = ann.get("/api/tasks/next", params={"kind": "find_again"}).json()["task"]
    assert (
        t["finding_id"] == "film:1"
        and t["question"] == "Tap it if you see it."
        and [a["id"] for a in t["answers"]] == ["seen", "not_seen", "cant_tell"]
    )
    assert ann.post(f"/api/me/tasks/{t['id']}/done", json={"result": "seen", "taps": []}).status_code == 422
    tap = {"ra": 10.004, "dec": 5.001, "pass": 1, "mjd": 61130.2}
    assert ann.post(f"/api/me/tasks/{t['id']}/done", json={"result": "seen", "taps": [tap]}).status_code == 200
    got = people.taps(st, "film:1", "find_again")
    assert len(got) == 1 and got[0]["pass"] == 1 and got[0]["ra"] == 10.004 and "@" not in str(got)
    assert ann.get("/api/tasks/next", params={"kind": "find_again"}).json()["task"]["finding_id"] == "film:2"  # not the same one twice
    t = bob.get("/api/tasks/next", params={"kind": "recentre"}).json()["task"]
    assert t["finding_id"] == "film:1" and t["answers"][0]["id"] == "done"
    bob.post(
        f"/api/me/tasks/{t['id']}/done",
        json={"result": "done", "taps": [{"ra": 10.0, "dec": 5.0, "frame": 0}, {"ra": 10.001, "dec": 5.0, "frame": 1}]},
    )
    assert [x["frame"] for x in people.taps(st, "film:1", "recentre")] == [0, 1]
    # "This picture is broken" from the lab, then a second look by another person
    assert ann.post("/api/me/exposures/flag", json={"exposure": "L2:2025-12-01:det3:0042", "ra": 10.0, "dec": 5.0}).json() == {
        "exposure": "L2:2025-12-01:det3:0042", "flags": 1, "bad": False}  # fmt: skip
    assert ann.get("/api/tasks/next", params={"kind": "bad_picture"}).json()["task"] is None  # not your own flag again
    t = bob.get("/api/tasks/next", params={"kind": "bad_picture"}).json()["task"]
    assert t["exposure"] == "L2:2025-12-01:det3:0042" and t["question"].startswith("Does this picture look broken")
    bob.post(f"/api/me/tasks/{t['id']}/done", json={"result": "broken"})
    assert people.bad_exposures(st) == {"L2:2025-12-01:det3:0042"} and people.people_bad_exposures(st) == {"L2:2025-12-01:det3:0042": 2}
    # a second look at a finding someone else submitted, judged by the verdict
    st.checks_status = lambda job: {"job": job, "state": "done", "can_submit": True, "checks": []}
    inv = ann.post("/api/me/investigations", json={"title": "Dot"}).json()
    fid = ann.post(f"/api/me/investigations/{inv['id']}/submit", json={"checks_job": "j"}).json()["submitted"]
    assert ann.get("/api/tasks/next", params={"kind": "second_look"}).json()["task"] is None  # not your own
    t = bob.get("/api/tasks/next", params={"kind": "second_look"}).json()["task"]
    assert t["finding_id"] == fid and [a["id"] for a in t["answers"]] == ["moves", "artefact", "cant_tell"]
    bob.post(f"/api/me/tasks/{t['id']}/done", json={"result": "moves"})
    db = accounts.get(st).db
    db.set_status(fid, "candidate")
    score.get(st, db).verdict(fid, "candidate")
    s = bob.get("/api/me/score").json()
    assert s["points"] == 10 and s["accuracy"] == 1.0  # a right second look; it counts for Peer


def test_talk_under_a_finding(site: tuple[FastAPI, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    app, _st = site
    user = _people(app)
    ann, bob = user("ann"), user("bob")
    guest = TestClient(app, base_url="http://localhost")
    monkeypatch.setattr(people, "COMMENT_GAP_S", 0.0)
    assert guest.post("/api/me/findings/film:1/comments", json={"text": "hi"}).status_code == 401
    c1 = ann.post(
        "/api/me/findings/film:1/comments", json={"text": "  It steps along in 4 of 5 pictures, see https://example.org/x  "}
    ).json()
    assert c1["name"] == "Ann" and c1["text"] == "It steps along in 4 of 5 pictures, see https://example.org/x" and c1["mine"] is True
    for text, why in (("<b>bold</b>", "plain text only"), ("![dot](https://example.org/a.png)", "no images"),
                      ("a https://a.io b https://b.io c https://c.io d https://d.io", "at most 3 links"), ("   ", "write something first")):  # fmt: skip
        r = ann.post("/api/me/findings/film:1/comments", json={"text": text})
        assert r.status_code == 422 and why in r.json()["detail"], text
    seen = guest.get("/api/findings/film:1/comments").json()["comments"]
    assert [(x["name"], x["mine"]) for x in seen] == [("Ann", False)] and "@" not in str(seen)
    # a report per person; hidden after three; nobody reports their own
    assert ann.post(f"/api/me/comments/{c1['id']}/report").status_code == 404
    for name in ("bob", "cid", "dee"):
        assert user(name).post(f"/api/me/comments/{c1['id']}/report").json() == {"reported": True}
    assert guest.get("/api/findings/film:1/comments").json()["comments"] == []
    assert ann.get("/api/findings/film:1/comments").json()["comments"][0]["hidden"] is True  # its author still sees it
    # rate limits: one every few seconds, twenty an hour
    monkeypatch.setattr(people, "COMMENT_GAP_S", 30.0)
    assert bob.post("/api/me/findings/film:1/comments", json={"text": "one"}).status_code == 200
    r = bob.post("/api/me/findings/film:1/comments", json={"text": "two"})
    assert r.status_code == 422 and "wait a few seconds" in r.json()["detail"]
    monkeypatch.setattr(people, "COMMENT_GAP_S", 0.0)
    codes = [bob.post("/api/me/findings/film:1/comments", json={"text": f"n{i}"}).status_code for i in range(25)]
    assert codes.count(429) >= 5
    mine = [x for x in bob.get("/api/findings/film:1/comments").json()["comments"] if x["mine"]]
    assert bob.delete(f"/api/me/comments/{mine[0]['id']}").status_code == 204
    assert ann.delete(f"/api/me/comments/{mine[1]['id']}").status_code == 404  # not hers


def test_a_hidden_find_is_a_control_object_and_judged_as_one(site: tuple[FastAPI, Any]) -> None:
    """The default list hides a star's residual: as a control of the series it still reaches a person (each fifth task), it is never
    an open task, and its answer is judged at once against the known one (here: a residual peak has no dot of its own)."""
    app, st = site
    residual = {"finding_id": "peak:residual", "kind": "peak", "status": "residual", "stage": "artefact", "ra": 20.0, "dec": 5.0,
                "field_key": "k9"}  # fmt: skip
    st.task_findings = lambda: [r for r in FINDS if r["finding_id"] != "film:known"]  # no known control among the leads
    st.task_controls = lambda: [residual]
    kim = _people(app)("kim")
    got = []
    for _ in range(5):
        t = kim.get("/api/tasks/next", params={"kind": "moves"}).json()["task"]
        got.append(t["finding_id"])
        path = ["no"] if t["finding_id"] == "peak:residual" else ["not_sure"]
        assert kim.post(f"/api/me/tasks/{t['id']}/done", json={"path": path}).status_code == 200
    assert got[:4] == ["film:1", "film:2", "mover:1", "peak:1"] and got[4] == "peak:residual"
    db = __import__("service.accounts", fromlist=["get"]).get(st).db
    ev = db._conn().execute("SELECT kind, right FROM acc_score_events WHERE ref='peak:residual'").fetchall()
    assert [(e["kind"], e["right"]) for e in ev] == [("control_peak", 1)]  # judged at once, right
    assert db._conn().execute("SELECT COUNT(*) FROM acc_series_results WHERE finding_id='peak:residual'").fetchone()[0] == 0


def test_the_controls_come_from_the_real_findings_query(tmp_path: Path) -> None:
    """Without stand-ins: a peak below its bar is hidden from the leads (the default list and "all") and is a control object."""
    from service.findings import FindingsDB
    from service.routes_tasks import control_rows, lead_findings

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds([
        {**base, "id": "peak:below", "kind": "peak", "data": {"peak": {"ok": False}}},
        {**base, "id": "film:lead", "kind": "film", "ra": 61.0, "data": {}},
    ])  # fmt: skip
    db.db.commit()
    st = SimpleNamespace(findings=SimpleNamespace(db=db))
    leads = lead_findings(st) or []
    assert "peak:below" not in {r["finding_id"] for r in leads} and "film:lead" in {r["finding_id"] for r in leads}
    assert "peak:below" not in {r["finding_id"] for r in lead_findings(st, "all") or []}
    ctl = control_rows(st)
    assert [(r["finding_id"], r["status"], r["stage"]) for r in ctl] == [("peak:below", "below", "artefact")]


def test_the_finds_list_is_read_once_for_a_while_and_each_caller_gets_copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A task asked for read every find three or four times (4–9 s on prod): find_rows keeps the list FIND_ROWS_KEEP_S."""
    from service import routes_tasks
    from service.findings import FindingsDB

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds([{**base, "id": "film:lead", "kind": "film", "ra": 61.0, "data": {}}])
    db.db.commit()
    st = SimpleNamespace(findings=SimpleNamespace(db=db))
    reads = []
    real = routes_tasks.lead_findings
    monkeypatch.setattr(routes_tasks, "lead_findings", lambda s, status=None: reads.append(status) or real(s, status))
    a = routes_tasks.find_rows(st)
    a[0]["status"] = "changed by a caller"
    b = routes_tasks.find_rows(st)
    assert len(reads) == 1 and b[0]["status"] != "changed by a caller" and b[0]["finding_id"] == "film:lead"
    # kept long enough: the kept list is handed out at once and read again in the background, once however many callers come
    # (an unchanged database is kept on: its age is renewed; a changed one is read: test_the_tasks_controls_and_quarters_read_the_finds_once)
    st._task_finds = (st._task_finds[0] - routes_tasks.FIND_ROWS_KEEP_S - 1, *st._task_finds[1:])
    old_at = st._task_finds[0]
    assert routes_tasks.find_rows(st)[0]["finding_id"] == "film:lead"
    routes_tasks.find_rows(st)
    st._task_finds_thread.join(5)
    assert len(reads) == 1 and st._task_finds[0] > old_at
    st._task_finds_read -= routes_tasks.FIND_ROWS_MAX_S  # and after FIND_ROWS_MAX_S it is read anyway
    st._task_finds = (st._task_finds[0] - routes_tasks.FIND_ROWS_KEEP_S - 1, *st._task_finds[1:])
    routes_tasks.find_rows(st)
    st._task_finds_thread.join(5)
    assert len(reads) == 2


def test_a_failed_background_read_of_the_finds_keeps_the_list_there_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from service import routes_tasks

    st = SimpleNamespace(_task_finds=(0.0, [{"finding_id": "film:old", "_listed": True}], None), _task_finds_read=0.0)

    def broken(s: Any, status: str | None = None) -> list[dict[str, Any]]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(routes_tasks, "lead_findings", broken)
    assert routes_tasks.find_rows(st) == [{"finding_id": "film:old"}]
    st._task_finds_thread.join(5)
    assert st._task_finds == (0.0, [{"finding_id": "film:old", "_listed": True}], None) and st._task_finds_busy is False
    assert "could not be read again" in caplog.text
    # the lock is free again: the next caller starts the next read
    monkeypatch.setattr(routes_tasks, "lead_findings", lambda s, status=None: [{"finding_id": "film:new", "_listed": True}])
    routes_tasks.find_rows(st)
    st._task_finds_thread.join(5)
    assert routes_tasks.find_rows(st) == [{"finding_id": "film:new"}]


def test_a_track_task_names_the_film_its_search_ran_on(site: tuple[FastAPI, Any]) -> None:
    """The lab opens a track's questions on the film the search made (warmed ahead), not a new film at another date."""
    app, st = site
    finds = [dict(f) for f in FINDS]
    finds[0] = {**finds[0], "data": {**finds[0]["data"], "film": 61100.4}}
    st.task_findings = lambda: finds
    ann = _people(app)("ann")
    t = ann.get("/api/tasks/next", params={"kind": "finding", "finding_id": "film:1"}).json()["task"]
    assert t["film"] == 61100.4
    t2 = ann.get("/api/tasks/next", params={"kind": "finding", "finding_id": "film:2"}).json()["task"]
    assert "film" not in t2  # a track whose search date is not kept: the lab picks the middle of the visits


def test_a_peak_jpl_names_is_no_lead_of_the_tasks(tmp_path: Path) -> None:
    """The task pool reads the JPL identification of the Planet X peaks as the Findings page does: a named body or an echo is known."""
    from service.findings import FindingsDB
    from service.routes_tasks import lead_findings

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 18, "ra": 26.24, "dec": -0.51, "rate": None, "snr": 8.8, "score": 8.8, "chance": 0.0, "scans": ["k"]}
    db.write_finds([{**base, "id": "peak:eris", "kind": "peak", "data": {"peak": {"ok": True}}}])
    db.db.commit()

    class Idn:
        def verdict_on(self, f: dict[str, Any]) -> dict[str, Any] | None:
            return {"state": "ok", "words": "an echo of Eris (JPL), not a new body"}

    st = SimpleNamespace(findings=SimpleNamespace(db=db))
    assert [r["status"] for r in lead_findings(st) or []] == ["unchecked"]
    st.fpx_known = Idn()
    assert [r["status"] for r in lead_findings(st, "any") or []] == ["known"]


def test_the_list_of_finds_is_read_at_the_start_once_the_findings_are_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from service import routes_tasks

    st = SimpleNamespace()
    calls = []

    def loading_then_ready(s: Any, status: str | None = None) -> list[dict[str, Any]] | None:
        calls.append(1)
        return None if len(calls) < 3 else [{"finding_id": "film:lead"}]  # None: the findings database is still loading

    monkeypatch.setattr(routes_tasks, "lead_findings", loading_then_ready)
    assert routes_tasks.prime_find_rows(st, tries=5, wait_s=0.0) is True
    assert len(calls) == 3 and st._task_finds[1] == [{"finding_id": "film:lead"}]


def test_a_node_without_findings_reads_no_list_at_the_start_and_says_no_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A1 has no findings table: its start logged a traceback (19 Nov 2026, Release 73)."""
    from service import routes_tasks

    def no_table(s: Any, status: str | None = None) -> list[dict[str, Any]]:
        raise sqlite3.OperationalError("no such table: finds")

    monkeypatch.setattr(routes_tasks, "lead_findings", no_table)
    assert routes_tasks.prime_find_rows(SimpleNamespace(), tries=3, wait_s=0.0) is False
    assert "Traceback" not in caplog.text and not [r for r in caplog.records if r.exc_info]


# --------------------------------------------------------------------------------------- Release 79: the answer counts, finds spread


def test_a_series_answer_is_a_check_of_the_find_my_findings_and_the_order_see_it(site: tuple[FastAPI, Any]) -> None:
    """The answer of a question series wrote only its own table: My findings, "Viewed by me", "checked by" and the order of the
    finds handed out read acc_credits `checked`, and saw nothing (20 Nov 2026). "Can't tell" is no check, as before."""
    app, st = site
    user = _people(app)
    ann = user("ann")
    t = ann.get("/api/tasks/next", params={"kind": "finding", "finding_id": "film:2"}).json()["task"]
    assert ann.post(f"/api/me/tasks/{t['id']}/done", json={"path": ["some", "done", "dot"], "pictures": [3]}).status_code == 200
    db = accounts.get(st).db
    assert db.check_counts() == {"film:2": 1}
    mine = ann.get("/api/me/findings").json()
    rows = mine.get("findings", mine) if isinstance(mine, dict) else mine
    assert any(r["finding_id"] == "film:2" and r["role"] == "checked" and r["result"] == "partial" for r in rows)
    assert [c["name"] for c in db.credits_of(["film:2"])["film:2"]] == ["Ann"]
    # answered twice (the task closed again): one check
    ann.post(f"/api/me/tasks/{t['id']}/done", json={"path": ["no"], "pictures": []})
    assert db.check_counts() == {"film:2": 1}


def test_two_people_in_a_row_are_given_different_finds(site: tuple[FastAPI, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Check a finding" handed every guest and every new person the same top find (the order by checks stood still, and among
    equals it was the page's first). A random one of the first FINDING_SPREAD equal ones; a find somebody checked goes below."""
    import random

    from service import routes_tasks

    app, st = site
    user = _people(app)
    got = set()
    for seed in range(10):
        monkeypatch.setattr(routes_tasks, "RNG", random.Random(seed))
        got.add(TestClient(app).get("/api/tasks/next", params={"kind": "finding"}).json()["task"]["finding_id"])
    assert len(got) > 1 and got <= {"film:1", "film:2", "mover:1", "peak:1"}  # the strong kinds' turn, spread
    # a find checked by one person goes below the unchecked ones of its group
    monkeypatch.setattr(routes_tasks, "RNG", random.Random(0))
    for name in ("ann", "bob", "cid"):
        c = user(name)
        t = c.get("/api/tasks/next", params={"kind": "finding"}).json()["task"]
        c.post(f"/api/me/tasks/{t['id']}/done", json={"path": ["no"], "pictures": []})
    checked = accounts.get(st).db.check_counts()
    assert sum(checked.values()) == 3 and len(checked) == 3  # three people, three different finds
    nxt = user("dee").get("/api/tasks/next", params={"kind": "finding"}).json()["task"]["finding_id"]
    assert nxt not in checked


def test_the_tasks_controls_and_quarters_read_the_finds_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One whole read of the finds for the tasks, the control objects and the promising quarters (each read it apart: three whole
    reads every two minutes and one every minute held the event loop 2–4 s on prod, 20 Nov 2026); an unchanged database is not
    read again when the list is old, a changed one is."""
    from service import routes_tasks
    from service.findings import FindingsDB

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds([
        {**base, "id": "peak:below", "kind": "peak", "data": {"peak": {"ok": False}}},
        {**base, "id": "film:lead", "kind": "film", "ra": 61.0, "data": {}},
    ])  # fmt: skip
    db.db.commit()
    st = SimpleNamespace(findings=SimpleNamespace(db=db))
    reads: list[Any] = []
    real = routes_tasks.lead_findings
    monkeypatch.setattr(routes_tasks, "lead_findings", lambda s, status=None: reads.append(status) or real(s, status))
    monkeypatch.setitem(routes_tasks._promising, "at", 0.0)
    assert [r["finding_id"] for r in routes_tasks.find_rows(st)] == ["film:lead"]
    assert [r["finding_id"] for r in routes_tasks.control_rows(st)] == ["peak:below"]
    routes_tasks.promising_quarters(st)
    assert reads == ["any"] and all("_listed" not in r for r in routes_tasks.find_rows(st) + routes_tasks.control_rows(st))
    # old, the database unchanged: not read again
    st._task_finds = (st._task_finds[0] - routes_tasks.FIND_ROWS_KEEP_S - 1, *st._task_finds[1:])
    routes_tasks.find_rows(st)
    st._task_finds_thread.join(5)
    assert reads == ["any"] and time_now_ok(st)
    # old, a find added: read again
    db.write_finds([
        {**base, "id": "peak:below", "kind": "peak", "data": {"peak": {"ok": False}}},
        {**base, "id": "film:lead", "kind": "film", "ra": 61.0, "data": {}},
        {**base, "id": "film:new", "kind": "film", "ra": 62.0, "data": {}},
    ])  # fmt: skip
    db.db.commit()
    st._task_finds = (st._task_finds[0] - routes_tasks.FIND_ROWS_KEEP_S - 1, *st._task_finds[1:])
    routes_tasks.find_rows(st)
    st._task_finds_thread.join(5)
    assert reads == ["any", "any"] and {r["finding_id"] for r in routes_tasks.find_rows(st)} == {"film:lead", "film:new"}


def time_now_ok(st: Any) -> bool:
    import time

    return time.monotonic() - st._task_finds[0] < 5


def test_the_findings_query_reads_the_tables_once_while_the_database_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from service import findings
    from service.findings import FindingsDB, query

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds([{**base, "id": "film:a", "kind": "film", "data": {"x": 1}}])
    db.db.commit()
    reads = []
    real = FindingsDB.ident_rows_of
    monkeypatch.setattr(FindingsDB, "ident_rows_of", lambda self, ids: reads.append(1) or real(self, ids))
    a = query(db, None, "any", None, 100, 0)
    a["finds"][0]["data"]["x"] = 99  # a caller's change stays its own
    a["finds"][0]["idents"].append({"catalogue": "mine"})
    b = query(db, None, "any", None, 100, 0)
    assert len(reads) == 1 and b["finds"][0]["data"] == {"x": 1} and all(i["catalogue"] != "mine" for i in b["finds"][0]["idents"])
    fa, fb = {**base, "id": "film:a", "kind": "film", "data": {"x": 1}}, {**base, "id": "film:b", "kind": "film", "ra": 61.0, "data": {}}
    db.write_finds([fa, fb])
    db.db.commit()
    c = query(db, None, "any", None, 100, 0)
    assert len(reads) == 2 and {f["id"] for f in c["finds"]} == {"film:a", "film:b"}
    # another connection's commit is seen too (the sweep writes through its own)
    other = FindingsDB(tmp_path / "findings.db", writer=True)
    other.write_finds([fa, fb, {**base, "id": "film:c", "kind": "film", "ra": 62.0, "data": {}}])
    other.db.commit()
    assert {f["id"] for f in query(db, None, "any", None, 100, 0)["finds"]} == {"film:a", "film:b", "film:c"} and len(reads) == 3
    assert findings.read_base is not None


def test_one_find_asked_by_id_is_read_alone_and_is_the_same_as_in_the_whole_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/v1/findings?ids=… (the lab's record of its find) read and judged every find: 6 s on prod for one (20 Nov 2026)."""
    from service import findings
    from service.findings import FindingsDB, query

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds([{**base, "id": f"film:{i}", "kind": "film", "ra": 60.0 + i, "data": {"i": i}} for i in range(30)])
    whole = {f["id"]: f for f in query(db, None, "any", None, 100, 0)["finds"]}
    monkeypatch.setattr(findings, "read_base", lambda d: (_ for _ in ()).throw(AssertionError("the whole list was read")))
    got = query(db, None, None, None, 100, 0, only=frozenset({"film:3", "film:7", "nope"}))
    assert [f["id"] for f in got["finds"]] == ["film:7", "film:3"] or {f["id"] for f in got["finds"]} == {"film:3", "film:7"}
    for f in got["finds"]:
        assert {k: v for k, v in f.items() if k != "updated"} == {k: v for k, v in whole[f["id"]].items() if k != "updated"}


def test_a_changed_database_reads_again_only_the_finds_that_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One catalogue's answer made every list read all 26,000 finds again, and the event loop stood for it (prod, 23 Nov 2026)."""
    from service import findings
    from service.findings import FindingsDB, query

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 8.0, "score": 8.0, "chance": 0.0, "scans": ["k"]}
    finds = [{**base, "id": f"film:{i}", "kind": "film", "ra": 60.0 + i, "data": {"i": i}} for i in range(1200)]
    db.write_finds(finds)
    db.put_ident("film:1", "gaia", "none", None, None, None, {"n": 1})
    db.db.commit()
    query(db, None, "any", None, 5, 0)
    made: list[int] = []
    real = findings.marshal.dumps
    monkeypatch.setattr(findings.marshal, "dumps", lambda x, *v: (made.append(1) if isinstance(x[0], dict) else None) or real(x, *v))

    def whole() -> dict[str, Any]:
        """What a connection that kept nothing reads."""
        fresh = FindingsDB(tmp_path / "findings.db", writer=True)
        return {f["id"]: (f["status"], f["data"], [(i["catalogue"], i["state"], i["detail"]) for i in f["idents"]]) for f in rows(fresh)}

    def rows(d: Any) -> list[dict[str, Any]]:
        return list(query(d, None, "any", None, 5000, 0)["finds"])

    def mine() -> dict[str, Any]:
        return {f["id"]: (f["status"], f["data"], [(i["catalogue"], i["state"], i["detail"]) for i in f["idents"]]) for f in rows(db)}

    db.put_ident("film:1", "gaia", "ok", "A star", 1.0, "star", {"n": 2})  # an answer changed in its place
    db.db.commit()
    got = mine()
    assert len(made) == 1 and got == whole()
    made.clear()
    other = FindingsDB(tmp_path / "findings.db", writer=True)  # another connection's answer, and one taken away
    other.put_ident("film:700", "sbident", "ok", "A body", 1.0, "asteroid", {})
    other.db.execute("DELETE FROM idents WHERE find_id='film:1'")
    other.db.commit()
    got = mine()
    assert len(made) == 2 and got == whole()
    made.clear()
    db.db.execute("DELETE FROM finds WHERE id='film:5'")  # a find gone is kept no more
    db.db.commit()
    got = mine()
    assert not made and "film:5" not in got and "film:5" not in db._kept_rows and len(db._kept_rows) == 1199


def test_a_number_that_turns_from_minus_one_into_minus_two_is_a_change(tmp_path: Path) -> None:
    """hash(-1) == hash(-2): the signature of a find's rows is not made of it."""
    from service.findings import FindingsDB, query, row_sig

    assert hash((1, -1)) == hash((1, -2)) and row_sig((1, -1), []) != row_sig((1, -2), [])
    assert row_sig((1, "a", None, -1.0), [("x", 2)]) == row_sig((1, "a", None, -1.0), [("x", 2)])
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 8.0, "score": 8.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds([{**base, "id": "film:a", "kind": "film", "data": {}}])
    db.put_ident("film:a", "gaia", "none", None, None, None, {})
    for dec, sep in ((-1.0, -1.0), (-2.0, -1.0), (-2.0, -2.0)):  # every other field stays (updated and tried too)
        db.db.execute("UPDATE finds SET dec=?", (dec,))
        db.db.execute("UPDATE idents SET sep=?", (sep,))
        db.db.commit()
        f = query(db, None, "any", None, 5, 0)["finds"][0]
        assert f["dec"] == dec and next(i for i in f["idents"] if i["catalogue"] == "gaia")["sep"] == sep


def test_an_empty_answer_of_the_version_pragma_counts_as_a_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The writer of the main instance writes through the same connection without the read lock; PRAGMA data_version came back empty
    beside it and the list answered 500 (prod, 20 Nov 2026). Such an answer is a change, never cached, never the same twice."""
    from service.findings import FindingsDB, query

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds([{**base, "id": "film:a", "kind": "film", "data": {}}])

    class Empty:
        def __init__(self, real: Any) -> None:
            self.real = real

        def execute(self, q: str, *a: Any) -> Any:
            return SimpleNamespace(fetchone=lambda: None) if q.startswith("PRAGMA data_version") else self.real.execute(q, *a)

        def __getattr__(self, k: str) -> Any:
            return getattr(self.real, k)

    real = db.db
    db.db = Empty(real)  # type: ignore[assignment]
    a, b = db.changes(), db.changes()
    assert a != b and a[0] < 0 and b[0] < 0
    assert [f["id"] for f in query(db, None, "any", None, 10, 0)["finds"]] == ["film:a"]
    assert db._base is None  # not kept: the version was not known
    db.db = real


def test_the_finds_are_read_from_many_threads_while_the_writer_writes(tmp_path: Path) -> None:
    """R79 under load: lists, single finds, the tasks' snapshot and its version read at once from several threads, the writer
    writing the list and the catalogue answers through the same connection meanwhile; no read fails."""
    import threading

    from service import routes_tasks
    from service.findings import FindingsDB, query

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    finds = [{**base, "id": f"film:{i}", "kind": "film", "ra": 60.0 + i * 0.01, "data": {}} for i in range(200)]
    db.write_finds(finds)
    st = SimpleNamespace(findings=SimpleNamespace(db=db))
    errors: list[BaseException] = []
    stop = threading.Event()

    def guard(fn: Any) -> Any:
        def run() -> None:
            try:
                while not stop.is_set():
                    fn()
            except BaseException as e:
                errors.append(e)
                stop.set()

        return run

    def writer() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            db.write_finds(finds[: 150 + n % 50])
            db.put_ident(f"film:{n % 150}", "sbident", "none", None, None, None, {}, 1)
            db.db.commit()

    jobs = [
        guard(lambda: query(db, None, None, None, 50, 0)),
        guard(lambda: query(db, None, "any", None, 50, 0, only=frozenset({"film:3"}))),
        guard(lambda: routes_tasks._rows_sig(st)),
        guard(lambda: routes_tasks._read_rows(st)),
        guard(writer),
    ]
    threads = [threading.Thread(target=j, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    stop.wait(4.0)
    stop.set()
    for t in threads:
        t.join(10)
    assert errors == []


def test_a_red_source_the_catalogues_answer_is_not_handed_out_and_a_known_peak_is_a_control(pp: People) -> None:
    rows = [
        {"finding_id": "red:1", "kind": "red", "status": "unexplained", "stage": None, "ask": False},
        {"finding_id": "red:2", "kind": "red", "status": "unexplained", "stage": None, "ask": True},
    ]
    got = pp.pick("red", rows, None, set())
    assert got is not None and got["finding_id"] == "red:2"
    assert pp.pick("red", rows[:1], None, set()) is None
    peak = {"finding_id": "peak:eris", "kind": "peak", "status": "known", "stage": "known"}
    assert pp.expected("peak", peak) == frozenset({"candidate"})  # a known body found blind: the dot is there
    assert pp.expected("peak", {**peak, "echo": True}) is None  # one pass of a body and noise: nobody knows what a person sees


def test_a_find_whose_picture_shows_nothing_is_not_asked_and_the_seen_ones_come_first(pp: People) -> None:
    row = {"kind": "film", "status": "unexplained", "stage": None, "ra": 1.0, "dec": 2.0}
    rows = [
        {**row, "finding_id": "film:empty", "visible": False},
        {**row, "finding_id": "film:unmeasured", "visible": None},
        {**row, "finding_id": "film:seen", "visible": True},
    ]
    assert pp.pick("moves", rows, None, set())["finding_id"] == "film:seen"  # type: ignore[index]
    assert pp.pick("moves", rows[:2], None, set())["finding_id"] == "film:unmeasured"  # type: ignore[index]
    assert pp.pick("moves", rows[:1], None, set()) is None
    assert pp.pick("moves", [{**rows[0], "raised": 1}], None, set()) is not None  # people saw it: asked whatever the picture says


def test_a_kind_switched_off_is_asked_of_nobody_not_even_as_a_control(pp: People, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(people, "KINDS_OFF", frozenset({"film"}))
    monkeypatch.setattr(people, "CONTROL_EVERY", 1)  # every task a control
    u = _users(pp, 1)[0]
    rows = [{**FINDS[0], "visible": True, "raised": 1}, {**FINDS[6], "visible": True}]
    assert pp.pick("moves", rows, u, set(), controls=lambda: [FINDS[6]]) is None
    assert pp.pick("moves", rows, None, set()) is None
    assert pp.pick("moves", [*rows, FINDS[3]], None, set())["finding_id"] == "peak:1"  # type: ignore[index]


def test_a_track_is_asked_when_its_dot_is_seen_in_half_of_its_visits() -> None:
    import importlib.util

    spec = importlib.util.find_spec("service.accounts.people")
    assert spec is not None and spec.loader is not None
    real = importlib.util.module_from_spec(spec)  # the rule itself (the fixture of the other tests stands it aside)
    spec.loader.exec_module(real)
    row = {"finding_id": "film:1", "kind": "film", "visible": True}
    seen = [({**row, "visits_seen": v}, ok) for v, ok in (((4, 4), True), ((2, 4), True), ((1, 4), False), ((0, 5), False), ((0, 0), False), (None, False))]
    assert [real.askable(r) for r, _ in seen] == [ok for _, ok in seen]
    assert not real.askable(row) and not real.askable({**row, "visible": False, "visits_seen": (4, 4)})
    assert real.askable({"kind": "peak", "visible": None}) and not real.askable({"kind": "peak", "visible": False})


def test_a_peak_without_its_two_pictures_is_not_asked() -> None:
    row = {"finding_id": "peak:1", "kind": "peak", "visible": True}
    assert people.askable(row) and people.askable({**row, "pictures": True}) and not people.askable({**row, "pictures": False})
    assert not people.askable({**row, "pictures": False, "raised": 1})
