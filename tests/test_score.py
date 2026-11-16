"""Score, achievements and the leaderboard (product spec §5a): every rule, and the rules against abuse."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import accounts
from service.accounts import register_accounts, score
from service.accounts.db import AccountsDB
from service.accounts.score import ACHIEVEMENTS, BY_ID, DAILY_SQUARES, PLUTO_1930, POINTS, Score, control_movers, level_of, place_facts
from service.config import Config

LOOK = {"seconds": 75, "passes_shown": 4, "passes_total": 4, "local_hour": 14}
BARNARD = next(m for m in control_movers() if m["name"] == "Barnard's Star")


@pytest.fixture
def sc(tmp_path: Path) -> Score:
    st = SimpleNamespace(finds={})
    st.score_findings = lambda: st.finds
    return Score(st, AccountsDB(tmp_path / "product.sqlite"))


def _user(sc: Score, name: str = "ann") -> str:
    uid: str = sc.db.user_for_email(f"{name}@example.org")["id"]
    return uid


def _square(cell: str, ra: float = 40.0, dec: float = 10.0, result: str = "nothing") -> dict[str, Any]:
    return {"cell_id": cell, "ra": ra, "dec": dec, "result": result}


def _points(sc: Score, uid: str) -> int:
    pts: int = sc.summary(uid)["points"]
    return pts


def _earned(sc: Score, uid: str) -> set[str]:
    return {a["id"] for a in sc.summary(uid)["achievements"] if a["earned_at"]}


def _answers(sc: Score, uid: str, n: int, answer: str, stage: str, start: int = 0, **facts: Any) -> None:
    for i in range(start, start + n):
        sc.st.finds[f"f{i}"] = {"stage": stage, "kind": "mover", **facts}
        sc.answered(uid, f"f{i}", answer)


# ------------------------------------------------------------------------------------------------------------------- squares


def test_a_square_pays_only_for_a_real_look_and_once(sc: Score) -> None:
    uid = _user(sc)
    assert sc.square_done(uid, _square("c1"), {**LOOK, "seconds": 59}, []) == 0  # the 60 s rule
    assert sc.square_done(uid, _square("c1"), {**LOOK, "passes_shown": 3}, []) == 0  # every pass shown
    assert sc.square_done(uid, _square("c1"), {}, []) == 0  # the lab said nothing about the look
    assert _points(sc, uid) == 0 and sc.summary(uid)["squares"] == 0
    assert sc.square_done(uid, _square("c1"), LOOK, []) == POINTS["square"] == 5
    assert sc.square_done(uid, _square("c1"), LOOK, []) == 0  # one count per user per square
    assert _points(sc, uid) == 5
    bob = _user(sc, "bob")
    assert sc.square_done(bob, _square("c1"), LOOK, []) == 5  # another person's look at the same square counts for them


def test_the_daily_cap_stops_the_points_not_the_count(sc: Score) -> None:
    uid = _user(sc)
    for i in range(DAILY_SQUARES + 3):
        sc.square_done(uid, _square(f"c{i}"), LOOK, [])
    s = sc.summary(uid)
    assert s["points"] == DAILY_SQUARES * 5 and s["squares"] == DAILY_SQUARES + 3
    # the squares of yesterday do not count against today
    sc.db._conn().execute("UPDATE acc_score_events SET at = at - 86400 * 2")
    assert sc.square_done(uid, _square("next-day"), LOOK, []) == 5


def test_no_points_for_a_mark_or_a_note(sc: Score) -> None:
    uid = _user(sc)
    sc.mark_put(uid)
    sc.db.add_investigation(uid, {"title": "x", "notes": [{"text": "n", "at": "t"}]})
    assert _points(sc, uid) == 0
    assert {"first_mark", "note_taker"} <= _earned(sc, uid)  # they are steps, not points


# ------------------------------------------------------------------------------------------------------------ control squares


def test_a_control_square_pays_for_the_marked_mover_and_counts_a_miss(sc: Score) -> None:
    uid = _user(sc)
    sq = _square("ctl", BARNARD["ra"], BARNARD["dec"], "marks")
    near = {"ra": BARNARD["ra"], "dec": BARNARD["dec"] + 20 / 3600}
    far = {"ra": BARNARD["ra"], "dec": BARNARD["dec"] + 200 / 3600}
    assert sc.control_in(40.0, 10.0) is None and sc.control_in(BARNARD["ra"], BARNARD["dec"])["name"] == "Barnard's Star"  # type: ignore[index]
    assert sc.square_done(uid, sq, {**LOOK, "seconds": 10}, [near]) == 0  # no real look: nothing, right or wrong
    assert sc.summary(uid)["accuracy"] is None
    assert sc.square_done(uid, sq, LOOK, [near]) == 5 + POINTS["control"]
    s = sc.summary(uid)
    assert s["accuracy"] == 1.0 and {"sharp_eye", "barnards_runner"} <= _earned(sc, uid)
    bob = _user(sc, "bob")
    assert sc.square_done(bob, sq, LOOK, [far]) == 5  # the square was looked through, the mover was missed
    assert sc.summary(bob)["accuracy"] == 0.0 and "sharp_eye" not in _earned(sc, bob)
    assert sc.square_done(bob, sq, LOOK, [near]) == 0  # a second try at the same control square does not count
    # only targets that move are control movers: the two variable stars of the test set are not
    assert {m["name"] for m in control_movers()} == {"Barnard's Star", "ε Indi B", "WISE 0855−0714"}


# ------------------------------------------------------------------------------------------------------------------- answers


def test_an_answer_is_paid_only_when_the_stage_is_final_and_agrees(sc: Score) -> None:
    uid = _user(sc)
    sc.st.finds["lead"] = {"stage": None, "kind": "mover"}  # an open lead: no final stage yet
    assert sc.answered(uid, "lead", "moves") == 0
    s = sc.summary(uid)
    assert s["points"] == 0 and s["waiting"] == 1 and s["accuracy"] is None
    sc.verdict("lead", "candidate")  # the verdict tool
    s = sc.summary(uid)
    assert s["points"] == POINTS["check"] == 10 and s["waiting"] == 0 and s["accuracy"] == 1.0
    sc.st.finds["star"] = {"stage": "known", "kind": "mover"}  # the catalogues already say what it is
    assert sc.answered(uid, "star", "moves") == 0  # disagrees: 0 points, and the accuracy falls
    assert sc.answered(uid, "star", "known") == 0  # the first answer stays; a second one is not taken
    s = sc.summary(uid)
    assert s["points"] == 10 and s["answered"] == 2 and s["accuracy"] == 0.5
    sc.st.finds["ghost"] = {"stage": "artefact", "kind": "anomaly"}
    assert sc.answered(uid, "ghost", "artefact") == 10
    assert sc.answered(uid, "ghost", "bogus") == 0
    kinds = [(e["kind"], e["points"], e["right"]) for e in sc.summary(uid)["events"]]
    assert kinds == [("check", 10, True), ("check", 0, False), ("check", 10, True)]


def test_nothing_is_ever_taken_away(sc: Score) -> None:
    uid = _user(sc)
    _answers(sc, uid, 3, "known", "known")
    before = _points(sc, uid)
    _answers(sc, uid, 5, "moves", "artefact", start=10)
    assert _points(sc, uid) == before == 30 and sc.summary(uid)["accuracy"] == pytest.approx(3 / 8)


# ------------------------------------------------------------------------------------------------------- submits and verdicts


def test_submit_candidate_confirmed(sc: Score) -> None:
    uid = _user(sc)
    inv = sc.db.add_investigation(uid, {"title": "Dot"})
    sub = sc.db.submit(uid, inv, "j", {}, [])
    fid = sub["finding_id"]
    sc.submitted(uid, fid, [{"id": "gaia", "state": "pass"}, {"id": "neowise", "state": "pass", "detail": "994 exposures added"}])
    sc.submitted(uid, fid, [])  # once
    assert _points(sc, uid) == 100 and {"first_submit", "clean_sheet", "patient"} <= _earned(sc, uid)
    sc.db.set_status(fid, "candidate")
    sc.verdict(fid, "candidate")
    assert _points(sc, uid) == 600 and "candidate" in _earned(sc, uid)
    sc.verdict(fid, "candidate")
    assert _points(sc, uid) == 600
    sc.db.set_status(fid, "confirmed")
    sc.verdict(fid, "confirmed")
    s = sc.summary(uid)
    assert s["points"] == 5600 and s["level"] == "Hunter" and "confirmed" in _earned(sc, uid)
    # a submit with warnings, or one the NEOWISE check did not apply to, is no clean sheet and not "patient"
    bob = _user(sc, "bob")
    sc.submitted(
        bob,
        "user:b",
        [{"id": "gaia", "state": "warn"}, {"id": "neowise", "state": "pass", "detail": "Not applicable to a body that moves this fast"}],
    )
    assert _earned(sc, bob) & {"clean_sheet", "patient"} == set() and "first_submit" in _earned(sc, bob)


def test_levels() -> None:
    assert [level_of(p)[0] for p in (0, 99, 100, 499, 500, 2500, 10000, 10**6)] == [
        "Observer", "Observer", "Searcher", "Searcher", "Tracker", "Hunter", "Discoverer", "Discoverer",
    ]  # fmt: skip
    assert level_of(0)[1] == 100 and level_of(10000)[1] is None


# -------------------------------------------------------------------------------------------------------------- achievements


def test_every_achievement_is_one_entry_with_a_rule() -> None:
    assert len(ACHIEVEMENTS) == len(BY_ID) == 57
    assert len({a.family for a in ACHIEVEMENTS}) == 14
    assert [a.id for a in ACHIEVEMENTS if a.hidden] == ["tombaugh", "night_owl", "second_opinion"]
    for a in ACHIEVEMENTS:
        have, need = a.rule(score.Stats())
        assert have == 0 and need >= 1, a.id  # nobody starts with one


def test_square_families_places_and_first_eyes(sc: Score) -> None:
    uid = _user(sc)
    for i in range(10):
        sc.square_done(uid, _square(f"p{i}", 40.0 + i * 0.4, 12.0), LOOK, [])  # in the Planet Nine band, north, nobody before
    got = _earned(sc, uid)
    assert {"first_light", "stargazer", "pioneer"} <= got and "both_skies" not in got and "surveyor" not in got
    sc.square_done(uid, _square("s", 200.0, -85.0), {**LOOK, "local_hour": 2}, [])
    got = _earned(sc, uid)
    assert {"both_skies", "polar_explorer", "night_owl"} <= got
    for i in range(10):
        sc.square_done(uid, _square(f"g{i}", 266.4 + i * 0.4, -29.0), LOOK, [])  # the galactic centre
    assert "milky_way" in _earned(sc, uid)
    facts = place_facts(*PLUTO_1930)
    assert facts["tombaugh"] and facts["ecliptic"] and not facts["p9"] and facts["hour"] == 7
    assert place_facts(26.75, -0.41)["far_world"] == "Eris" and place_facts(40.0, 12.0)["p9"]
    sc.square_done(uid, _square("pluto", *PLUTO_1930), LOOK, [])
    assert "tombaugh" in _earned(sc, uid)
    # Far world: the square must be looked through AND marked
    sc.square_done(uid, _square("eris", 26.75, -0.41, "nothing"), LOOK, [])
    assert "far_world" not in _earned(sc, uid)
    bob = _user(sc, "bob")
    sc.square_done(bob, _square("eris", 26.75, -0.41, "marks"), LOOK, [])
    assert "far_world" in _earned(sc, bob)
    hidden = [a for a in sc.summary(bob)["achievements"] if a["hidden"]]
    assert [(a["name"], a["line"], a["progress"]) for a in hidden] == [("?", "A hidden achievement", 0.0)] * 3
    prog = {a["id"]: (a["have"], a["need"], a["progress"]) for a in sc.summary(uid)["achievements"]}
    assert prog["surveyor"] == (23, 50, 0.46) and prog["around_the_clock"][1] == 24


def test_first_eyes_needs_nobody_before(sc: Score) -> None:
    uid = _user(sc)
    sc.db._conn().executescript(
        "CREATE TABLE grid_views (cell_id TEXT, viewer TEXT, result TEXT, at REAL);"
        f"INSERT INTO grid_views VALUES ('seen','anon:x','nothing',1.0),('mine','user:{uid}','nothing',1.0);"
    )
    for cell in ("seen", "mine"):
        sc.square_done(uid, _square(cell), LOOK, [])
    first = {e["ref"]: e["detail"]["first"] for e in sc.events(uid)}
    assert first == {"seen": False, "mine": True}  # the lab records the user's own view before it closes the task


def test_accuracy_and_catalogue_achievements(sc: Score) -> None:
    uid = _user(sc)
    _answers(sc, uid, 10, "known", "known")
    _answers(sc, uid, 10, "known", "known", start=10, jpl=True)
    _answers(sc, uid, 10, "artefact", "artefact", start=20, rank=3)
    got = _earned(sc, uid)
    assert {"first_check", "catalogue_reader", "asteroid_wrangler", "artefact_spotter", "never_fooled", "second_opinion"} <= got
    assert "librarian" not in got and "nine_of_ten" not in got
    bob = _user(sc, "bob")
    _answers(sc, bob, 19, "known", "known")
    _answers(sc, bob, 1, "moves", "known", start=50)  # one wrong answer breaks the run
    _answers(sc, bob, 5, "known", "known", start=60)
    assert "never_fooled" not in _earned(sc, bob)
    _answers(sc, bob, 80, "known", "known", start=100)  # 104 right of 105
    assert {"nine_of_ten", "never_fooled"} <= _earned(sc, bob) and "flawless_hundred" not in _earned(sc, bob)


def test_peer_measurer_time_traveller_and_days(sc: Score) -> None:
    uid, bob = _user(sc), _user(sc, "bob")
    for i in range(10):
        inv = sc.db.add_investigation(bob, {"title": f"b{i}"})
        fid = sc.db.submit(bob, inv, "j", {}, [])["finding_id"]
        sc.answered(uid, fid, "moves")
        sc.db.set_status(fid, "candidate")
        sc.verdict(fid, "candidate")
    assert "peer" in _earned(sc, uid) and "three_candidates" in _earned(sc, bob) and "case_closed" in _earned(sc, bob)
    inv = sc.db.add_investigation(uid, {"title": "m"})
    sc.db.patch_investigation(uid, inv["id"], {"extra": {"ruler": {"rate": 12.5, "catalogue_rate": 10.4}}})
    assert "measurer" not in _earned(sc, uid)  # 20% off at most
    sc.db.patch_investigation(uid, inv["id"], {"extra": {"ruler": {"rate": 11.0, "catalogue_rate": 10.4}}})
    sc.db.patch_investigation(uid, inv["id"], {"extra": {"old_survey": {"survey": "2MASS"}}})
    assert {"measurer", "time_traveller"} <= _earned(sc, uid)
    # three days in a row: squares on three consecutive days
    for d in range(3):
        sc.square_done(uid, _square(f"d{d}"), LOOK, [])
        sc.db._conn().execute("UPDATE acc_score_events SET at = at - 86400 WHERE kind='square'")
    got = _earned(sc, uid)
    assert "days_3" in got and "days_7" not in got


# --------------------------------------------------------------------------------------------------------------- leaderboard


def test_leaderboard_ranks_only_after_ten_answers_and_hides_on_request(sc: Score) -> None:
    ann, bob, cid = _user(sc), _user(sc, "bob"), _user(sc, "cid")
    _answers(sc, ann, 12, "known", "known")  # 120 points, 12 answers
    _answers(sc, bob, 10, "known", "known", start=100)
    _answers(sc, bob, 10, "moves", "known", start=200)  # 100 points, accuracy 0.5
    inv = sc.db.add_investigation(cid, {"title": "lucky"})
    sc.submitted(cid, sc.db.submit(cid, inv, "j", {}, [])["finding_id"], [])  # 100 points, no answers: not ranked
    rows = sc.leaders("week", cid)["rows"]
    assert [(r["place"], r["name"], r["points"], r["accuracy"], r["me"]) for r in rows] == [
        (1, "ann", 120, 1.0, False), (2, "bob", 100, 0.5, False), (None, "cid", 100, None, True),
    ]  # fmt: skip
    assert rows[2]["answers_to_rank"] == 10 and "email" not in str(rows) and "@" not in str(rows)
    assert [r["name"] for r in sc.leaders("all", None)["rows"]] == ["ann", "bob"]  # a guest sees the ranked only
    sc.db._conn().execute("UPDATE acc_users SET hide_board=1 WHERE id=?", (ann,))
    assert [(r["place"], r["name"]) for r in sc.leaders("week", None)["rows"]] == [(1, "bob")]
    mine = sc.leaders("week", ann)["rows"]
    assert [(r["place"], r["name"], r["me"]) for r in mine] == [(1, "bob", False), (None, "ann", True)]  # hidden, still pinned for herself
    assert _points(sc, ann) == 120  # and she still earns
    # last month's points are not this week's
    sc.db._conn().execute("UPDATE acc_score_events SET at = at - 40 * 86400 WHERE user_id=?", (bob,))
    assert sc.leaders("week", None)["rows"] == [] and [r["name"] for r in sc.leaders("all", None)["rows"]] == ["bob"]
    # the row shows the three rarest badges
    assert len(sc.leaders("all", bob)["rows"][0]["badges"]) <= 3


def test_rarity_is_the_share_of_searchers_and_shown_from_fifty(sc: Score, monkeypatch: pytest.MonkeyPatch) -> None:
    ann, bob = _user(sc), _user(sc, "bob")
    sc.square_done(ann, _square("a"), LOOK, [])
    sc.square_done(bob, _square("b"), {**LOOK, "local_hour": 1}, [])
    sc.summary(ann)
    assert {a["rarity"] for a in sc.summary(bob)["achievements"]} == {None}  # two searchers: a share says nothing
    monkeypatch.setattr(score, "RARITY_MIN_SEARCHERS", 2)
    r = {a["id"]: a["rarity"] for a in sc.summary(bob)["achievements"]}
    assert r["first_light"] == 100.0 and r["night_owl"] == 50.0 and r["stargazer"] == 0.0


# ------------------------------------------------------------------------------------------------------------- through the API


def test_score_through_the_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    monkeypatch.setenv("SKY_AUTH_DEV", "1")
    st = SimpleNamespace(cfg=Config(store_dir=tmp_path), salt="s")
    app = FastAPI()
    register_accounts(app, st)
    c = TestClient(app, base_url="http://localhost", follow_redirects=False)
    assert c.get("/api/me/score").status_code == 401 and c.get("/api/leaders").json()["rows"] == []
    link = c.post("/api/auth/email", json={"email": "ann@example.org"}).json()["dev_link"]
    c.get(link.replace("http://localhost", ""))
    accounts.get(st).db._conn().executescript(
        "CREATE TABLE grid_cells (id TEXT PRIMARY KEY, state TEXT, finds INTEGER, field_key TEXT, searched_at TEXT, updated REAL);"
        "CREATE TABLE grid_views (cell_id TEXT, viewer TEXT, result TEXT, at REAL);"
        "INSERT INTO grid_cells VALUES ('294-0148','searched',0,'k1','2026-11-16',0);"
    )
    t = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    quick = c.post(
        f"/api/me/tasks/{t['id']}/done", json={"result": "nothing", "mark_ids": [], "seconds": 5, "passes_shown": 1, "passes_total": 4}
    )
    assert quick.json()["points"] == 0  # a glance earns nothing, and the task is closed: no second try at it
    again = c.post(f"/api/me/tasks/{t['id']}/done", json={"result": "nothing", "mark_ids": [], **LOOK}).json()
    assert again["points"] == 0
    t2 = c.get("/api/tasks/next", params={"kind": "cell", "cell_id": "294-0149"}).json()["task"]
    out = c.post(f"/api/me/tasks/{t2['id']}/done", json={"result": "nothing", "mark_ids": [], **LOOK}).json()
    assert out["points"] == 5 and [a["name"] for a in out["achievements"]] == ["First light"]
    s = c.get("/api/me/score").json()
    assert s["points"] == 5 and s["level"] == "Observer" and s["next_level_at"] == 100 and s["hide_from_board"] is False
    assert s["rules"]["points"]["confirmed"] == 5000 and len(s["achievements"]) == 57
    assert c.patch("/api/me", json={"hide_from_board": True}).status_code == 200
    assert c.get("/api/me/score").json()["hide_from_board"] is True
    assert c.patch("/api/me", json={"name": "Ann K."}).json()["user"]["name"] == "Ann K."
    assert any(n["text"].startswith("Achievement: Named") for n in c.get("/api/me/notifications").json()["notifications"])
    mine = c.get("/api/leaders", params={"period": "all"}).json()["rows"]
    assert [(r["place"], r["name"], r["points"], r["me"]) for r in mine] == [(None, "Ann K.", 5, True)]
    assert c.get("/api/leaders", params={"period": "year"}).status_code == 422
    assert time.time() > 0
