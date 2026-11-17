"""Tracks as people corrected them (service/people_tracks.py): missing visits left out of the fit, re-centred visits moved to the
median of two people's taps, found-again places joined only when the track still fits."""

from __future__ import annotations

import math
from typing import Any

from service import people_tracks as pt
from tests.test_accounts import site  # noqa: F401 (the fixture)

# a body moving 36″/h due east (pa 90°) at dec 0, five visits an hour apart; visit 2 is a wrong detection 60″ off
T0 = 61114.0
VISITS = [{"ra": 100.0 + 36.0 * h / 3600.0, "dec": 0.0, "mjd": T0 + h / 24.0} for h in range(5)]
VISITS[2] = {"ra": 100.0 + 72.0 / 3600.0, "dec": 60.0 / 3600.0, "mjd": T0 + 2 / 24.0}


def find() -> dict[str, Any]:
    return {"id": "film:abc123", "kind": "film", "data": {"visits": [dict(v) for v in VISITS], "track": {"rate": 30.0}}}


def test_the_fit_of_a_straight_track() -> None:
    f = pt.fit([v for i, v in enumerate(VISITS) if i != 2])
    assert f is not None and math.isclose(f["rate"], 36.0, rel_tol=1e-3) and math.isclose(f["pa"], 90.0, abs_tol=0.5) and f["rms"] < 0.01
    assert pt.fit(VISITS[:1]) is None


def test_missing_visits_are_left_out_and_the_track_refitted() -> None:
    assert pt.apply(find(), None) == find() and pt.apply(find(), {"missing": set(), "recentre": [], "find_again": []}) == find()
    out = pt.apply(find(), {"missing": {2}, "recentre": [], "find_again": []})
    tr = out["data"]["people_track"]
    assert tr["state"] == "relinked" and tr["missing"] == [2] and tr["n"] == 4 and math.isclose(tr["rate"], 36.0, rel_tol=1e-3)
    assert out["data"]["track"] == {"rate": 30.0}  # the machine's own track stays
    few = pt.apply(find(), {"missing": {0, 1, 2, 3}, "recentre": [], "find_again": []})
    assert few["data"]["people_track"]["state"] == "too few visits left"


def test_two_peoples_recentre_taps_move_a_visit_one_does_not() -> None:
    right = {"ra": 100.0 + 72.0 / 3600.0, "dec": 0.0, "mjd": T0 + 2 / 24.0 + 0.001}
    one = pt.apply(find(), {"missing": set(), "recentre": [{**right, "user": "u1"}], "find_again": []})
    assert one == find()  # one person is not agreement
    two = pt.apply(find(), {"missing": set(), "recentre": [{**right, "user": "u1"}, {**right, "user": "u2"}], "find_again": []})
    tr = two["data"]["people_track"]
    assert tr["recentred"] == 1 and tr["rms"] < 0.01 and tr["visits"][2]["recentred"] is True


def test_a_found_again_place_joins_only_when_the_track_still_fits() -> None:
    on_track = {"ra": 100.0 + 36.0 * 10 / 3600.0, "dec": 0.0, "mjd": T0 + 10 / 24.0}  # ten hours later, on the line
    off_track = {"ra": 100.5, "dec": 0.3, "mjd": T0 + 10 / 24.0}
    said = {"missing": {2}, "recentre": [], "find_again": [{**on_track, "user": "u1"}, {**on_track, "user": "u2"}]}
    tr = pt.apply(find(), said)["data"]["people_track"]
    assert tr["found_again"] == 1 and tr["n"] == 5 and tr["visits"][-1]["found_again"] is True
    said_off = {"missing": {2}, "recentre": [], "find_again": [{**off_track, "user": "u1"}, {**off_track, "user": "u2"}]}
    assert pt.apply(find(), said_off)["data"]["people_track"]["found_again"] == 0  # it would break the fit
    lone = {"missing": set(), "recentre": [], "find_again": [{**on_track, "user": "u1"}]}
    assert pt.apply(find(), lone) == find()  # one person's tap is not agreement


def test_read_gathers_what_people_said_in_bulk(site: Any) -> None:  # noqa: F811
    from service import accounts
    from service.accounts import people

    _app, st, _mail = site
    db = accounts.get(st).db
    people.get(st, db)
    c = db._conn()
    c.execute(
        "INSERT INTO acc_people_actions(finding_id, action, reason, data, at) VALUES(?,?,?,?,?)",
        ("film:abc123", "relink", "partial", '{"missing": [2]}', 1.0),
    )
    for u in ("u1", "u2"):
        c.execute("INSERT INTO acc_taps(kind, finding_id, user_id, pass, frame, mjd, ra, dec, at) VALUES(?,?,?,?,?,?,?,?,?)",
                  ("recentre", "film:abc123", u, 0, 2, T0 + 2 / 24.0, 100.02, 0.0, 1.0))  # fmt: skip
    said = pt.read(st)
    assert said["film:abc123"]["missing"] == {2} and len(said["film:abc123"]["recentre"]) == 2
