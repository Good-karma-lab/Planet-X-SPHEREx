# ruff: noqa: E501
"""What people do for the machine (product spec §5e): the question series, what each answer does, the other task kinds, talk,
control objects and weights, the counts for the Method page."""

from __future__ import annotations

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
    assert first["track"]["question"] == "Does one dot step along the arrow?"
    assert [a["label"] for a in first["track"]["answers"]] == ["Yes, in every picture", "Only in some", "No", "Not sure"]
    assert steps_of("track")[1]["question"] == "Tap the pictures where it is missing." and steps_of("track")[1]["tap"] is True
    assert [a["label"] for a in steps_of("track")[2]["answers"]] == ["Dot", "Streak", "Not sure"]
    assert first["slow"]["question"] == "Does the dot in the ring shift while the stars stay put?"
    assert steps_of("slow")[1]["question"] == "Is it one dot, or two touching?"
    assert steps_of("slow")[2]["question"] == "Old pictures (WISE 2010, 2MASS 2000): is there a dot along the arrow, further back?"
    assert first["peak"]["question"].startswith("Left: added along the orbit. Right: added as it is.")
    assert steps_of("peak")[1]["question"] == "Is it inside a bright star's glow or on its spike?"
    assert [s["question"] for s in steps_of("red")] == [
        "A sharp dot, or a fuzzy patch?",
        "Is it there every time?",
        "Is it on a bright star's glow, a spike, or the picture edge?",
        "Old picture next to new: has it moved?",
    ]
    assert [s["question"] for s in steps_of("anomaly")] == [
        "In the odd pictures, does a neighbour star touch it?",
        "Do the odd pictures look broken (a streak, cut off, glare)?",
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
    assert t["series"]["id"] == "track" and [s["id"] for s in t["series"]["steps"]] == ["steps", "missing", "shape"]
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
    assert counts["series"]["track"]["steps"]["steps"] == {"some": 3} and counts["series"]["track"]["results"] == {"partial": 3}


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
