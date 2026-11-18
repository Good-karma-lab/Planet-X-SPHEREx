"""Reviewers: only an account granted on the server sees the review queue and gives verdicts; each verdict is logged with who and
why, pays and settles as the command line's did, and tells everyone credited."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI

from service import accounts
from service.accounts.review import Review
from tests.test_accounts import _client, _Mail, _sign_in, _State, _unread, site  # noqa: F401  (the fixture)


def _submission(st: _State, email: str, fid: str = "user:abc123") -> str:
    db = accounts.get(st).db
    uid = db._conn().execute("SELECT id FROM acc_users WHERE email=?", (email,)).fetchone()["id"]
    now = time.time()
    db._conn().execute(
        "INSERT INTO acc_submissions(finding_id,user_id,investigation_id,checks_job,title,ra,dec,status,checks,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (fid, uid, "inv-1", "j1", "A dot that moves", 10.5, -3.25, "submitted", json.dumps([{"id": "gaia", "state": "pass"}]), now, now),
    )
    db.credit(fid, uid, "submitted")
    return fid


def test_only_a_reviewer_gives_verdicts(site: tuple[FastAPI, _State, _Mail]) -> None:  # noqa: F811
    app, st, mail = site
    ann = _sign_in(app, mail, "ann@example.org")
    rev = _sign_in(app, mail, "rev@example.org")
    fid = _submission(st, "ann@example.org")
    assert _client(app).get("/api/review").status_code == 401
    assert rev.get("/api/review").status_code == 403 and rev.get("/api/me").json()["user"]["reviewer"] is False
    rv = Review(accounts.get(st).db)
    assert rv.grant("nobody@example.org") is False and rv.grant("REV@example.org") is True
    assert rev.get("/api/me").json()["user"]["reviewer"] is True and ann.get("/api/me").json()["user"]["reviewer"] is False

    q = rev.get("/api/review").json()
    assert [x["finding_id"] for x in q["waiting"]] == [fid] and q["waiting"][0]["by"] == "ann"
    assert q["waiting"][0]["checks"][0]["id"] == "gaia" and "example.org" not in json.dumps(q)
    assert ann.post(f"/api/review/{fid}", json={"status": "candidate"}).status_code == 403  # the submitter is not a reviewer
    assert rev.post(f"/api/review/{fid}", json={"status": "submitted"}).status_code == 422  # the machine's own stage
    assert rev.post(f"/api/review/{fid}", json={"status": "artefact"}).status_code == 422  # says why
    assert rev.post("/api/review/user:nope", json={"status": "candidate"}).status_code == 404
    before = _unread(ann)
    out = rev.post(f"/api/review/{fid}", json={"status": "candidate", "note": "JPL and MPC list nothing; seen in 3 passes"}).json()
    assert out["status"] == "candidate" and out["told"] == 1 and out["history"][0]["by"] == "rev"
    assert ann.get("/api/me/findings").json()["findings"][0]["status"] == "candidate" and _unread(ann) == before + 1
    pts = accounts.get(st).db._conn().execute("SELECT SUM(points) FROM acc_score_events WHERE kind='candidate'").fetchone()[0]
    assert pts == 500  # the submitter's candidate points, as the command line paid them
    q = rev.get("/api/review").json()
    assert q["waiting"] == [] and q["log"][0]["status"] == "candidate" and q["log"][0]["note"].startswith("JPL")
    hist = _client(app).get(f"/api/findings/{fid}/verdicts").json()["verdicts"]
    assert [(h["status"], h["by"]) for h in hist] == [("candidate", "rev")] and "example.org" not in json.dumps(hist)
    assert rv.revoke("rev@example.org") and rev.get("/api/review").status_code == 403


def test_the_command_line_grants_and_logs(site: tuple[FastAPI, _State, _Mail], monkeypatch: Any, capsys: Any) -> None:  # noqa: F811
    app, st, mail = site
    _sign_in(app, mail, "ann@example.org")
    fid = _submission(st, "ann@example.org")
    from service.accounts import verdict

    monkeypatch.setattr(verdict, "load", lambda: st.cfg)
    assert verdict.main(["--grant", "ann@example.org"]) == 0 and verdict.main(["--reviewers"]) == 0
    assert verdict.main([fid, "known", "2003 AB12 (MPC)"]) == 0
    assert verdict.main(["user:nope", "known"]) == 1 and verdict.main([fid, "new"]) == 2
    out = capsys.readouterr().out
    assert "granted" in out and "ann" in out and "1 people notified" in out
    hist = Review(accounts.get(st).db).history(fid)
    assert hist == [{"status": "known", "by": "command line", "note": "2003 AB12 (MPC)", "at": hist[0]["at"]}]
