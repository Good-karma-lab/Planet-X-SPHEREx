"""The judged task kinds of product spec §5b: does it really move, what is this red source, what is this spectral anomaly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from service.accounts import score
from service.accounts.agreement import AGREE_N, ANSWERS, MAX_ANSWERS, Agreement
from service.accounts.db import AccountsDB


@pytest.fixture
def ag(tmp_path: Path) -> Agreement:
    st = SimpleNamespace(finds={})
    st.score_findings = lambda: st.finds
    return Agreement(score.Score(st, AccountsDB(tmp_path / "product.sqlite")))


def _users(ag: Agreement, n: int) -> list[str]:
    return [ag.db.user_for_email(f"u{i}@example.org")["id"] for i in range(n)]


def _score(ag: Agreement, uid: str) -> tuple[int, float | None]:
    s = ag.score.summary(uid)
    return s["points"], s["accuracy"]


def test_the_answer_sets_of_the_spec() -> None:
    assert [label for _, label in ANSWERS["moves"]] == ["It moves", "It stands still", "It is an artefact", "Can't tell"]
    assert [label for _, label in ANSWERS["red"]] == [
        "Cold brown dwarf",
        "Dusty or reddened star",
        "Galaxy or extended",
        "Artefact",
        "Can't tell",
    ]
    assert [label for _, label in ANSWERS["anomaly"]] == [
        "Real feature",
        "Detector edge or ghost",
        "Cosmic ray or one bad exposure",
        "Can't tell",
    ]
    assert (AGREE_N, MAX_ANSWERS) == (3, 5)


def test_three_agreeing_answers_settle_a_red_source_and_pay_those_who_agreed(ag: Agreement) -> None:
    a, b, c, d = _users(ag, 4)
    place = {"ra": 10.0, "dec": -5.0}
    assert ag.answer(a, "red", "red:1", "brown_dwarf", place) == 0  # nothing is known yet
    assert ag.answer(b, "red", "red:1", "dusty_star", place) == 0
    assert ag.answer(c, "red", "red:1", "brown_dwarf", place) == 0
    assert ag.people_say(["red:1"]) == {} and _score(ag, a) == (0, None)
    assert ag.answer(d, "red", "red:1", "brown_dwarf", place) == 10  # the third agreeing answer settles it
    said = ag.people_say(["red:1"])["red:1"]
    assert (said["result"], said["agree"], said["total"], said["text"]) == ("brown_dwarf", 3, 4, "people say: cold brown dwarf, 3 of 4")
    assert [_score(ag, u) for u in (a, b, c, d)] == [(10, 1.0), (0, 0.0), (10, 1.0), (10, 1.0)]
    # people called it a cold brown dwarf: it is in the queue of the brown-dwarf search
    assert [(q["finding_id"], q["ra"], q["dec"], q["agree"], q["total"]) for q in ag.brown_dwarf_queue()] == [("red:1", 10.0, -5.0, 3, 4)]
    assert ag.answer(a, "red", "red:1", "galaxy", place) == 0 and _score(ag, a) == (10, 1.0)  # the first answer stays


def test_five_answers_settle_without_three_agreeing(ag: Agreement) -> None:
    u = _users(ag, 5)
    for uid, ans in zip(u, ["real", "detector", "real", "cosmic_ray", "cant_tell"], strict=True):
        ag.answer(uid, "anomaly", "anomaly:1", ans)
    said = ag.people_say(["anomaly:1"])["anomaly:1"]
    assert (said["result"], said["agree"], said["total"], said["text"]) == ("real", 2, 5, "people say: real feature, 2 of 5")
    assert [_score(ag, x) for x in u] == [(10, 1.0), (0, 0.0), (10, 1.0), (0, 0.0), (0, None)]  # "Can't tell": not wrong, not paid
    assert ag.brown_dwarf_queue() == []
    # a tie has no result: nobody is right or wrong
    v = _users(ag, 9)[5:]
    for uid, ans in zip(v, ["real", "detector", "real", "detector"], strict=True):
        ag.answer(uid, "anomaly", "anomaly:2", ans)
    assert ag.people_say(["anomaly:2"]) == {}
    ag.answer(u[0], "anomaly", "anomaly:2", "cant_tell")
    assert ag.people_say(["anomaly:2"])["anomaly:2"]["text"] == "people do not agree (5 answers)"
    assert [_score(ag, x) for x in v] == [(0, None)] * 4


def test_cant_tell_alone_never_makes_a_result_before_five(ag: Agreement) -> None:
    u = _users(ag, 5)
    for uid in u[:4]:
        assert ag.answer(uid, "red", "red:2", "cant_tell") == 0
    assert ag.people_say(["red:2"]) == {}
    ag.answer(u[4], "red", "red:2", "galaxy")
    assert ag.people_say(["red:2"])["red:2"]["text"] == "people say: galaxy or extended, 1 of 5"
    assert _score(ag, u[4]) == (10, 1.0) and _score(ag, u[0]) == (0, None)


def test_does_it_move_is_judged_by_the_final_stage(ag: Agreement) -> None:
    a, b, c, d = _users(ag, 4)
    ag.score.st.finds["m1"] = {"stage": None, "kind": "mover"}  # an open lead
    assert ag.answer(a, "moves", "m1", "moves") == 0 and ag.answer(b, "moves", "m1", "still") == 0
    assert ag.answer(c, "moves", "m1", "cant_tell") == 0 and ag.answer(d, "moves", "m1", "bogus") == 0
    assert _score(ag, a) == (0, None)
    ag.score.verdict("m1", "candidate")
    assert [_score(ag, u) for u in (a, b, c, d)] == [(10, 1.0), (0, 0.0), (0, None), (0, None)]
    # a known mover does move; a residual stands still or is an artefact (the stage does not tell which)
    ag.score.st.finds.update({"star": {"stage": "known", "kind": "mover"}, "resid": {"stage": "artefact", "kind": "mover"}})
    assert ag.answer(a, "moves", "star", "moves") == 10 and ag.answer(b, "moves", "star", "artefact") == 0
    assert ag.answer(a, "moves", "resid", "still") == 10 and ag.answer(c, "moves", "resid", "artefact") == 10
    assert ag.answer(d, "moves", "resid", "moves") == 0 and _score(ag, d) == (0, 0.0)
    assert ag.people_say(["m1", "star"]) == {}  # no people's result for this kind: the stage decides


def test_which_find_is_asked_next(ag: Agreement) -> None:
    a, b = _users(ag, 2)
    rows = [
        {"finding_id": "red:1", "kind": "red"}, {"finding_id": "red:2", "kind": "red"}, {"finding_id": "red:3", "kind": "red"},
        {"finding_id": "anomaly:1", "kind": "anomaly"}, {"finding_id": "mover:1", "kind": "mover", "stage": "known"},
        {"finding_id": "film:1", "kind": "film", "stage": None},
    ]  # fmt: skip
    assert ag.pick("red", rows, a, set())["finding_id"] == "red:1"  # type: ignore[index]
    ag.answer(b, "red", "red:2", "galaxy")
    assert ag.pick("red", rows, a, set())["finding_id"] == "red:2"  # type: ignore[index]  # an answered one is the nearest to a result
    assert ag.pick("red", rows, b, set())["finding_id"] == "red:1"  # type: ignore[index]  # not the one b answered
    assert ag.pick("red", rows, a, {"red:2", "red:1"})["finding_id"] == "red:3"  # type: ignore[index]  # nor one a has a task for
    assert ag.pick("anomaly", rows, a, set())["finding_id"] == "anomaly:1"  # type: ignore[index]
    assert ag.pick("moves", rows, a, set())["finding_id"] == "film:1"  # type: ignore[index]  # an open lead before a settled one
    for uid in _users(ag, 5):
        ag.answer(uid, "red", "red:3", "artefact")
    assert "red:3" not in {(ag.pick("red", rows, None, s) or {}).get("finding_id") for s in (set(), {"red:1"}, {"red:1", "red:2"})}
