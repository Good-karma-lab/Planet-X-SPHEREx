"""Accounts: sign-in by a single-use link, the session cookie, the user's data, tasks and submit (product spec §8).

The grid and the checks of the other streams are stood in for here by small fakes behind the same contracts."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import accounts
from service.accounts import COOKIE, register_accounts
from service.config import Config


class _State:
    def __init__(self, store: Path) -> None:
        self.cfg = Config(store_dir=store)
        self.salt = "s"


class _Mail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_link(self, email: str, link: str, minutes: int) -> None:
        self.sent.append((email, link))


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, _State, _Mail]:
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    for k in ("SKY_AUTH_DEV", "SKY_PUBLIC_URL", "SKY_GOOGLE_CLIENT_ID", "SKY_GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    st = _State(tmp_path)
    app = FastAPI()
    register_accounts(app, st)
    mail = _Mail()
    accounts.get(st).mail = mail
    return app, st, mail


def _unread(c: TestClient) -> int:
    """Unread notifications, the achievements apart (they have their own tests)."""
    return sum(1 for n in c.get("/api/me/notifications").json()["notifications"] if not n["read"] and n["kind"] != "achievement")


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://localhost", follow_redirects=False)


def _sign_in(app: FastAPI, mail: _Mail, email: str = "ann@example.org") -> TestClient:
    c = _client(app)
    r = c.post("/api/auth/email", json={"email": email})
    assert r.status_code == 200 and r.json() == {"sent": True, "minutes": 15}
    link = mail.sent[-1][1]
    r = c.get(link.replace("http://localhost", ""))
    assert r.status_code == 303 and r.headers["location"] == "/#/me"
    return c


MARK = {"id": "m-0001-abcdef", "field_key": "f" * 24, "cell_id": "b10c20", "ra": 10.5, "dec": -3.25, "pass": 2, "label": "dot",
        "level": "suspicious", "note": "moves left", "created": "2026-11-16T15:00:00Z"}  # fmt: skip


def test_guest_gets_401_and_me_says_null(site: tuple[FastAPI, _State, _Mail]) -> None:
    c = _client(site[0])
    assert c.get("/api/me").json() == {"user": None, "auth": {"email": True, "google": False, "dev": False}}
    for path in ("marks", "tasks", "history", "notifications", "investigations", "findings"):
        assert c.get(f"/api/me/{path}").status_code == 401
    assert c.post("/api/me/marks", json=MARK).status_code == 401
    assert c.post("/api/findings/submit", json={"investigation_id": "x", "checks_job": "j"}).status_code == 401


def test_link_signs_in_once_and_the_cookie_is_httponly(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, _, mail = site
    c = _client(app)
    c.post("/api/auth/email", json={"email": " Ann@Example.org "})
    email, link = mail.sent[-1]
    assert email == "ann@example.org"
    r = c.get(link.replace("http://localhost", ""))
    cookie = r.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" not in cookie
    me = c.get("/api/me").json()["user"]
    assert me["email"] == "ann@example.org" and me["name"] == "ann"
    # the same link a second time, in another browser: refused
    other = _client(app)
    r = other.get(link.replace("http://localhost", ""))
    assert r.status_code == 303 and r.headers["location"].endswith("#/me?signin=expired")
    assert other.get("/api/me").json()["user"] is None
    # nothing of the token or the session is stored in the clear, and there is no password column at all
    db = sqlite3.connect(accounts.get(site[1]).db.path)
    token = re.search(r"token=(.+)$", link).group(1)  # type: ignore[union-attr]
    dump = "\n".join(db.iterdump())
    assert token not in dump and c.cookies[COOKIE] not in dump and "password" not in dump.lower()


def test_link_expires(site: tuple[FastAPI, _State, _Mail], monkeypatch: pytest.MonkeyPatch) -> None:
    app, st, mail = site
    c = _client(app)
    c.post("/api/auth/email", json={"email": "ann@example.org"})
    accounts.get(st).db._conn().execute("UPDATE acc_login_tokens SET expires = expires - 3600")
    r = c.get(mail.sent[-1][1].replace("http://localhost", ""))
    assert r.headers["location"].endswith("signin=expired") and c.get("/api/me").json()["user"] is None


def test_dev_link_only_in_dev_mode_and_next_is_kept(site: tuple[FastAPI, _State, _Mail], monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, _ = site
    c = _client(app)
    assert "dev_link" not in c.post("/api/auth/email", json={"email": "a@example.org"}).json()
    monkeypatch.setenv("SKY_AUTH_DEV", "1")
    link = c.post("/api/auth/email", json={"email": "a@example.org", "next": "#/lab?ra=10&dec=2"}).json()["dev_link"]
    assert c.get(link.replace("http://localhost", "")).headers["location"] == "/#/lab?ra=10&dec=2"
    # a place outside the app is not followed
    link = c.post("/api/auth/email", json={"email": "a@example.org", "next": "//evil.example"}).json()["dev_link"]
    assert c.get(link.replace("http://localhost", "")).headers["location"] == "/#/me"


def test_behind_the_web_dev_server_the_link_goes_to_the_page(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, _, mail = site
    c = TestClient(app, base_url="http://127.0.0.1:8783", follow_redirects=False)  # the proxy puts the API's address into Host
    assert c.post("/api/auth/email", json={"email": "ann@example.org"}, headers={"origin": "http://localhost:5183"}).status_code == 200
    assert mail.sent[-1][1].startswith("http://localhost:5183/api/auth/callback?token=")
    assert c.post("/api/auth/email", json={"email": "ann@example.org"}, headers={"origin": "https://evil.example"}).status_code == 403


def test_sign_in_is_rate_limited_and_bad_addresses_refused(site: tuple[FastAPI, _State, _Mail]) -> None:
    c = _client(site[0])
    assert c.post("/api/auth/email", json={"email": "not an address"}).status_code == 422
    codes = [c.post("/api/auth/email", json={"email": "ann@example.org"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]  # three links per address
    codes = [c.post("/api/auth/email", json={"email": f"p{i}@example.org"}).status_code for i in range(3)]
    assert codes[-1] == 429  # five per client


def test_public_server_needs_its_address_and_a_secure_cookie(site: tuple[FastAPI, _State, _Mail], monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, mail = site
    c = TestClient(app, base_url="https://www.spherex.space", follow_redirects=False)
    assert c.post("/api/auth/email", json={"email": "ann@example.org"}).status_code == 503  # the Host header is not trusted
    monkeypatch.setenv("SKY_PUBLIC_URL", "https://www.spherex.space")
    forged = c.post("/api/auth/email", json={"email": "ann@example.org"}, headers={"host": "evil.example"})
    assert forged.status_code == 200 and mail.sent[-1][1].startswith("https://www.spherex.space/api/auth/callback?token=")
    r = c.get(mail.sent[-1][1].replace("https://www.spherex.space", ""))
    assert "Secure" in r.headers["set-cookie"]
    # a changing request sent by another site's page is refused
    assert c.post("/api/auth/logout", headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/me/marks", json=MARK, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/me/marks", json=MARK, headers={"origin": "https://www.spherex.space"}).status_code == 200


def test_marks_are_kept_per_user_and_seen_from_a_second_browser(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, _, mail = site
    a = _sign_in(app, mail)
    assert a.post("/api/me/marks", json=MARK).json() == {**MARK, "mjd": None}
    dated = a.post("/api/me/marks", json={**MARK, "mjd": 61118.31214}).json()  # the lab's date of the frame is kept and given back
    assert dated["mjd"] == 61118.31214 and a.get("/api/me/marks").json()["marks"][0]["mjd"] == 61118.31214
    assert a.post("/api/me/marks", json={**MARK, "mjd": 12.0}).status_code == 422
    a.post("/api/me/marks", json=MARK)
    assert a.post("/api/me/marks", json={**MARK, "label": "dot 2"}).json()["label"] == "dot 2"  # the same id replaces
    made = a.post("/api/me/marks", json={"field_key": "k1", "ra": 1, "dec": 2, "level": "artefact"}).json()
    assert made["id"] and made["created"] and made["pass"] is None and made["cell_id"] is None
    assert a.post("/api/me/marks", json={**MARK, "level": "odd"}).status_code == 422
    second = _sign_in(app, mail)  # the same person in another browser
    assert {m["id"] for m in second.get("/api/me/marks").json()["marks"]} == {MARK["id"], made["id"]}
    assert [m["id"] for m in second.get("/api/me/marks", params={"field_key": "k1"}).json()["marks"]] == [made["id"]]
    bob = _sign_in(app, mail, "bob@example.org")
    assert bob.get("/api/me/marks").json() == {"marks": []}
    assert bob.post("/api/me/marks", json=MARK).status_code == 409  # somebody else's id
    assert bob.delete(f"/api/me/marks/{MARK['id']}").status_code == 404
    assert a.delete(f"/api/me/marks/{MARK['id']}").status_code == 204
    assert a.delete("/api/me/marks", params={"id": made["id"]}).status_code == 204
    assert second.get("/api/me/marks").json() == {"marks": []}
    # signing out ends this browser's session only
    assert a.post("/api/auth/logout").json() == {"user": None}
    assert a.get("/api/me/marks").status_code == 401 and second.get("/api/me/marks").status_code == 200


def test_history_notifications_and_name(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, st, mail = site
    c = _sign_in(app, mail)
    assert c.patch("/api/me", json={"name": "Ann K."}).json()["user"]["name"] == "Ann K."
    for ref in ("#/lab?ra=1&dec=2", "#/explore", "#/lab?ra=1&dec=2"):
        assert c.post("/api/me/history", json={"kind": "lab", "ref": ref, "title": "a field"}).status_code == 200
    h = c.get("/api/me/history").json()["history"]
    assert [x["ref"] for x in h] == ["#/lab?ra=1&dec=2", "#/explore"]  # the same place moves to the top
    assert c.post("/api/me/history", json={"kind": "lab", "ref": "https://evil.example"}).status_code == 422
    # the lab's address changes while the user works in one field: the place is one entry, with the latest link
    for band in ("1", "2"):
        c.post("/api/me/history", json={"kind": "lab", "ref": f"#/lab?ra=9&dec=9&band={band}", "key": "lab:9,9,20"})
    h = c.get("/api/me/history").json()["history"]
    assert len(h) == 3 and h[0]["ref"] == "#/lab?ra=9&dec=9&band=2"
    uid = c.get("/api/me").json()["user"]["id"]
    accounts.get(st).db.notify(uid, "status", "Finding x: the status is now “candidate”.", "x")
    n = c.get("/api/me/notifications").json()
    assert _unread(c) == 1 and next(x for x in n["notifications"] if x["kind"] == "status")["ref"] == "x"
    assert any(x["text"].startswith("Achievement: Named.") for x in n["notifications"])  # the name was chosen above
    c.post("/api/me/notifications/read", json={})
    assert c.get("/api/me/notifications").json()["unread"] == 0
    assert c.delete("/api/me/history").status_code == 204 and c.get("/api/me/history").json() == {"history": []}


def _fake_grid(st: _State) -> None:
    """The grid stream's tables in the product database, with three searched squares (the contract of service/grid.py)."""
    db = accounts.get(st).db._conn()
    db.executescript(
        "CREATE TABLE grid_cells (id TEXT PRIMARY KEY, state TEXT, finds INTEGER, field_key TEXT, searched_at TEXT);"
        "CREATE TABLE grid_views (cell_id TEXT, viewer TEXT, result TEXT, at REAL);"
        "INSERT INTO grid_cells VALUES ('294-0148','searched',0,'k1','2026-11-16'),('294-0149','searched',3,'k2','2026-11-16'),"
        "('294-0150','searched',0,'k3','2026-11-16'),('294-0151','queued',0,NULL,NULL);"
        "INSERT INTO grid_views VALUES ('294-0149','anon:x','nothing',1.0);"
    )


def test_cell_tasks_walk_the_least_viewed_squares(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, st, mail = site
    guest = _client(app)
    assert guest.get("/api/tasks/next", params={"kind": "cell"}).status_code == 503  # no grid yet
    _fake_grid(st)
    t = guest.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert t["kind"] == "cell" and t["cell_id"] == "294-0148" and t["field_key"] == "k1"
    assert guest.post(f"/api/me/tasks/{t['id']}/done", json={"result": "nothing", "mark_ids": []}).status_code == 401
    c = _sign_in(app, mail)
    t1 = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert t1["cell_id"] == "294-0148"
    assert c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]["id"] == t1["id"]  # the open task comes back
    assert c.post(f"/api/me/tasks/{t1['id']}/done", json={"result": "moves", "mark_ids": []}).status_code == 422
    done = c.post(f"/api/me/tasks/{t1['id']}/done", json={"result": "marks", "mark_ids": ["m1"]}).json()["task"]
    assert done["state"] == "done" and done["result"] == "marks" and done["mark_ids"] == ["m1"] and done["done_at"]
    t2 = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert t2["cell_id"] == "294-0150"  # c2 has a viewer already, so it comes last
    c.post(f"/api/me/tasks/{t2['id']}/done", json={"result": "nothing", "mark_ids": []})
    t3 = c.get("/api/tasks/next", params={"kind": "cell", "cell_id": "294-0151"}).json()["task"]  # "Take this square" on the globe
    assert t3["cell_id"] == "294-0151" and t3["field_key"] is None
    assert [x["cell_id"] for x in c.get("/api/me/tasks").json()["tasks"]] == ["294-0151", "294-0150", "294-0148"]
    bob = _sign_in(app, mail, "bob@example.org")
    assert bob.post(f"/api/me/tasks/{t3['id']}/done", json={"result": "nothing", "mark_ids": []}).status_code == 404
    # a task taken as a guest is closed after signing in
    assert bob.post(f"/api/me/tasks/{t['id']}/done", json={"result": "nothing", "mark_ids": []}).json()["task"]["state"] == "done"


def test_finding_tasks_and_the_credit(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, st, mail = site
    c = _sign_in(app, mail)
    assert c.get("/api/tasks/next", params={"kind": "finding"}).status_code == 503  # no findings database
    finds = [
        {"finding_id": "mover:1", "field_key": "k1", "ra": 1.0, "dec": 2.0, "status": "known"},
        {"finding_id": "mover:2", "field_key": "k2", "ra": 3.0, "dec": 4.0, "status": "unexplained"},
        {"finding_id": "mover:3", "field_key": "k3", "ra": 5.0, "dec": 6.0, "status": "unchecked"},
    ]
    st.task_findings = lambda: finds  # type: ignore[attr-defined]
    t = c.get("/api/tasks/next", params={"kind": "finding"}).json()["task"]
    assert t["finding_id"] == "mover:2" and t["field_key"] == "k2" and t["cell_id"] is None
    c.post(f"/api/me/tasks/{t['id']}/done", json={"result": "moves", "mark_ids": []})
    assert c.get("/api/tasks/next", params={"kind": "finding"}).json()["task"]["finding_id"] == "mover:3"
    bob = _sign_in(app, mail, "bob@example.org")
    assert bob.get("/api/tasks/next", params={"kind": "finding"}).json()["task"]["finding_id"] == "mover:3"  # nobody checked it yet
    assert bob.get("/api/tasks/next", params={"kind": "finding", "finding_id": "nope"}).status_code == 404
    credit = accounts.get(st).db.credits_of(["mover:2"])["mover:2"]
    assert [(x["name"], x["role"], x["result"]) for x in credit] == [("ann", "checked", "moves")]
    mine = c.get("/api/me/findings").json()["findings"]
    assert [(f["finding_id"], f["role"], f["status"]) for f in mine] == [("mover:2", "checked", "in_review")]


def test_investigation_and_submit_only_when_the_checks_allow(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, st, mail = site
    c = _sign_in(app, mail)
    c.post("/api/me/marks", json=MARK)
    inv = c.post("/api/me/investigations", json={"title": "Dot near Barnard", "mark_ids": [MARK["id"]]}).json()
    assert inv["notes"] == [] and inv["frames"] == [] and inv["checks"] is None and inv["submitted"] is None
    iid = inv["id"]
    frame = {"ref": "#/lab?ra=10.5&dec=-3.25", "title": "pass 2", "note": "here", "thumb": "data:image/webp;base64,AAAA"}
    assert len(c.post(f"/api/me/investigations/{iid}/frames", json=frame).json()["frames"]) == 1
    assert c.post(f"/api/me/investigations/{iid}/frames", json={**frame, "thumb": "javascript:1"}).status_code == 422
    assert c.patch(f"/api/me/investigations/{iid}", json={"title": "Dot", "checks_job": "j1"}).json()["title"] == "Dot"
    sub = {"investigation_id": iid, "checks_job": "j1"}
    assert c.post("/api/findings/submit", json=sub).status_code == 503  # the checks stream is not loaded

    jobs: dict[str, Any] = {"j1": {"state": "running", "checks": [{"id": "gaia", "state": "pass", "detail": ""}], "can_submit": False}}
    st.checks_status = lambda job: jobs.get(job)  # type: ignore[attr-defined]  # the contract of GET /api/checks/{job}
    assert c.post("/api/findings/submit", json={**sub, "checks_job": "nope"}).status_code == 404
    assert c.post("/api/findings/submit", json=sub).status_code == 409  # still running
    jobs["j1"] = {"state": "done", "checks": [{"id": "gaia", "state": "fail", "detail": "a Gaia star"}], "can_submit": False}
    assert c.post("/api/findings/submit", json=sub).status_code == 409  # a check failed
    assert c.get("/api/me/investigations").json()["investigations"][0]["checks"]["can_submit"] is False
    jobs["j1"] = {"state": "done", "checks": [{"id": "gaia", "state": "pass", "detail": ""}, {"id": "edge_bright", "state": "warn",
                  "detail": "near the edge"}], "can_submit": True}  # fmt: skip
    assert c.post("/api/findings/submit", json=sub).status_code == 409  # a warning needs a comment
    c.post(f"/api/me/investigations/{iid}/notes", json={"text": "The edge is 40 px away; seen in three passes."})
    bob = _sign_in(app, mail, "bob@example.org")
    assert bob.post("/api/findings/submit", json=sub).status_code == 404  # not Bob's investigation
    out = c.post("/api/findings/submit", json=sub).json()
    assert out["finding_id"].startswith("user:") and out["status"] == "submitted"
    assert c.post("/api/findings/submit", json=sub).json()["finding_id"] == out["finding_id"]  # once
    mine = c.get("/api/me/findings").json()["findings"]
    assert len(mine) == 1 and mine[0]["role"] == "submitted" and mine[0]["ra"] == MARK["ra"] and mine[0]["field_key"] == MARK["field_key"]
    public = _client(app).get("/api/findings/submitted").json()["findings"]
    assert [(x["name"], x["role"]) for x in public[0]["credit"]] == [("ann", "submitted"), ("ann", "marked")]
    assert "email" not in str(public)
    assert _unread(c) == 1
    # the verdict reaches the user
    assert accounts.get(st).db.set_status(out["finding_id"], "candidate") == 1
    assert c.get("/api/me/findings").json()["findings"][0]["status"] == "candidate"
    assert _unread(c) == 2
    assert c.delete(f"/api/me/investigations/{iid}").status_code == 204


def test_google_sign_in_with_a_fake_google(site: tuple[FastAPI, _State, _Mail], monkeypatch: pytest.MonkeyPatch) -> None:
    app, st, _ = site
    c = _client(app)
    assert c.get("/api/auth/google").status_code == 404  # hidden when not set up
    monkeypatch.setenv("SKY_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("SKY_GOOGLE_CLIENT_SECRET", "secret")
    assert c.get("/api/me").json()["auth"]["google"] is True

    def google(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            assert b"client_secret=secret" in request.content and b"code=the-code" in request.content
            return httpx.Response(200, json={"access_token": "at"})
        assert request.headers["authorization"] == "Bearer at"
        return httpx.Response(200, json={"sub": "g-1", "email": "Ann@Example.org", "email_verified": True, "name": "Ann K."})

    accounts.get(st).http_transport = httpx.MockTransport(google)
    r = c.get("/api/auth/google")
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?client_id=cid&")
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)  # type: ignore[union-attr]
    assert c.get("/api/auth/google/callback", params={"code": "the-code", "state": "forged"}).headers["location"].endswith("signin=google")
    r = c.get("/api/auth/google/callback", params={"code": "the-code", "state": state})
    assert r.headers["location"] == "/#/me"
    assert c.get("/api/me").json()["user"] == {"id": c.get("/api/me").json()["user"]["id"], "name": "Ann K.", "email": "ann@example.org"}


def test_notifications_say_what_happened_once(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, st, mail = site
    _fake_grid(st)
    accounts.get(st).db._conn().execute("ALTER TABLE grid_cells ADD COLUMN updated REAL DEFAULT 0")
    c = _sign_in(app, mail)
    t = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    c.post(f"/api/me/tasks/{t['id']}/done", json={"result": "nothing", "mark_ids": []})
    jobs: dict[str, Any] = {"j1": {"state": "running", "checks": [], "can_submit": False}}
    st.checks_status = lambda job: jobs.get(job)  # type: ignore[attr-defined]
    c.post("/api/me/investigations", json={"title": "Dot", "checks_job": "j1"})
    assert c.get("/api/me/notifications").json()["notifications"] == []
    jobs["j1"] = {"state": "done", "checks": [], "can_submit": True}
    accounts.get(st).db._conn().execute("UPDATE grid_cells SET finds=2, updated=strftime('%s','now')+10 WHERE id=?", (t["cell_id"],))
    texts = sorted(n["text"] for n in c.get("/api/me/notifications").json()["notifications"])
    assert texts == [
        f"Square {t['cell_id']}, which you looked through, now has 2 machine finds.",
        "The checks of “Dot” have finished: ready to submit.",
    ]
    assert len(c.get("/api/me/notifications").json()["notifications"]) == 2  # said once


def test_download_and_delete_my_data(site: tuple[FastAPI, _State, _Mail]) -> None:
    app, st, mail = site
    c = _sign_in(app, mail)
    c.post("/api/me/marks", json=MARK)
    c.post("/api/me/history", json={"kind": "lab", "ref": "#/lab?ra=1&dec=2"})
    r = c.get("/api/me/export")
    assert "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["user"]["email"] == "ann@example.org" and data["marks"] == [{**MARK, "mjd": None}] and len(data["history"]) == 1
    other = _sign_in(app, mail)
    assert _client(app).delete("/api/me").status_code == 401
    assert c.delete("/api/me").status_code == 204
    assert c.get("/api/me").json()["user"] is None and other.get("/api/me").json()["user"] is None  # every session ended
    dump = "\n".join(sqlite3.connect(accounts.get(st).db.path).iterdump())
    assert "ann@example.org" not in dump and MARK["id"] not in dump
    again = _sign_in(app, mail)  # the address can start anew, empty
    assert again.get("/api/me/marks").json() == {"marks": []}


def test_declared_submit_finishes_by_itself(site: tuple[FastAPI, _State, _Mail]) -> None:
    """ "Check and submit" is said once; the server submits when the checks allow it, with no page open (st.on_checks_done)."""
    app, st, mail = site
    c = _sign_in(app, mail)
    c.post("/api/me/marks", json=MARK)
    jobs: dict[str, Any] = {}
    st.checks_status = lambda job: jobs.get(job)  # type: ignore[attr-defined]

    def job(state: str, *checks: tuple[str, str, str], ok: bool = False) -> dict[str, Any]:
        return {"job": "x", "state": state, "checks": [{"id": i, "state": s, "detail": d} for i, s, d in checks], "can_submit": ok}

    def texts() -> list[str]:
        return [n["text"] for n in c.get("/api/me/notifications").json()["notifications"] if n["kind"] != "achievement"]

    def new(title: str) -> str:
        iid: str = c.post("/api/me/investigations", json={"title": title, "mark_ids": [MARK["id"]]}).json()["id"]
        return iid

    # 1. every check passes: declared while running, submitted by the checks' own signal
    a = new("Clean")
    assert c.post(f"/api/me/investigations/{a}/submit", json={"checks_job": "nope"}).status_code == 404
    jobs["ja"] = job("running", ("gaia", "pass", ""), ("jpl", "running", "Asking JPL for the bodies of visit 2 of 5"))
    inv = c.post(f"/api/me/investigations/{a}/submit", json={"checks_job": "ja", "note": "Seen in both passes."}).json()
    assert inv["state"] == "checking" and inv["declared"] and len(inv["notes"]) == 1
    assert inv["progress"] == "1 of 2 checks answered · Asking JPL for the bodies of visit 2 of 5"
    jobs["ja"] = job("done", ("gaia", "pass", ""), ("jpl", "pass", ""), ok=True)
    st.on_checks_done({"job": "ja"})  # type: ignore[attr-defined]  # the checks stream's signal: nobody has a page open
    mine = c.get("/api/me/investigations").json()["investigations"]
    done = next(i for i in mine if i["id"] == a)
    assert done["state"] == "submitted" and done["submitted"].startswith("user:")
    assert texts() == ["Your finding “Clean” is submitted and waits for the verdict."]
    st.on_checks_done({"job": "ja"})  # type: ignore[attr-defined]  # a second signal changes nothing
    assert len(texts()) == 1

    # 2. a check fails: the user is told which and why, nothing is submitted
    b = new("A star")
    jobs["jb"] = job("done", ("gaia", "fail", "Gaia DR3 1 stands 1.1″ from every mark: a known object."), ("jpl", "pass", ""))
    assert c.post(f"/api/me/investigations/{b}/submit", json={"checks_job": "jb"}).json()["state"] == "blocked"
    assert texts()[0] == "“A star” was not submitted. Gaia: Gaia DR3 1 stands 1.1″ from every mark: a known object."
    assert len(c.get("/api/me/findings").json()["findings"]) == 1

    # a fail is final at once: the slow checks still running do not keep the user waiting
    b2 = new("A star, JPL still asked")
    jobs["jb2"] = job("running", ("gaia", "fail", "a known object"), ("jpl", "running", "visit 1 of 24"))
    assert c.post(f"/api/me/investigations/{b2}/submit", json={"checks_job": "jb2"}).json()["state"] == "blocked"
    assert texts()[0] == "“A star, JPL still asked” was not submitted. Gaia: a known object"

    # a check that did not answer and is asked again by the job is not a refusal: the submit keeps waiting, nothing is said
    n = new("JPL is away")
    jobs["jn"] = {
        **job("done", ("gaia", "pass", ""), ("jpl", "no_answer", "JPL did not answer")),
        "next_try_at": "2026-11-16T20:26:58+00:00",
    }
    waits = c.post(f"/api/me/investigations/{n}/submit", json={"checks_job": "jn"}).json()
    assert waits["state"] == "checking" and waits["progress"] == "1 of 2 checks answered · JPL did not answer and is asked again"
    assert not any("JPL is away" in t for t in texts())
    jobs["jn"] = job("done", ("gaia", "pass", ""), ("jpl", "pass", ""), ok=True)
    st.on_checks_done({"job": "jn"})  # type: ignore[attr-defined]
    assert texts()[0] == "Your finding “JPL is away” is submitted and waits for the verdict."

    # 3. two warnings: comments are asked for once, and the submit happens by itself when they are there
    w = new("Fast")
    jobs["jw"] = job("running", ("straight_line", "running", ""))
    c.post(f"/api/me/investigations/{w}/submit", json={"checks_job": "jw"})
    jobs["jw"] = job("done", ("straight_line", "warn", "curved"), ("gaia", "warn", "near a star"), ("jpl", "pass", ""), ok=True)
    st.on_checks_done({"job": "jw"})  # type: ignore[attr-defined]
    assert texts()[0].startswith("“Fast”: every check has answered. 2 comments are needed for the warnings (straight line, Gaia)")
    one = c.post(f"/api/me/investigations/{w}/notes", json={"text": "The path curves: the body is close."}).json()
    assert one["state"] == "needs_comment" and one["submitted"] is None
    assert texts()[0].startswith("“Fast”: every check has answered. One comment is needed")
    two = c.post(f"/api/me/investigations/{w}/notes", json={"text": "The star is near the first visit only."}).json()
    assert two["state"] == "submitted" and two["submitted"]
    assert texts()[0] == "Your finding “Fast” is submitted and waits for the verdict."
    assert len(c.get("/api/me/findings").json()["findings"]) == 3


def test_sign_in_opens_soon_without_a_mail_sender_and_google(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    for k in ("SKY_AUTH_DEV", "SKY_PUBLIC_URL", "SKY_GOOGLE_CLIENT_ID", "SKY_GOOGLE_CLIENT_SECRET", "SKY_RESEND_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    st = _State(tmp_path)
    app = FastAPI()
    register_accounts(app, st)
    c = _client(app)
    assert c.get("/api/me").json() == {"user": None, "auth": {"email": False, "google": False, "dev": False}}
    r = c.post("/api/auth/email", json={"email": "ann@example.org"})
    assert r.status_code == 503 and r.json()["detail"] == "Sign-in opens soon"
    db = sqlite3.connect(accounts.get(st).db.path)
    assert db.execute("SELECT COUNT(*) FROM acc_login_tokens").fetchone()[0] == 0  # the address was not taken
    assert c.get("/api/me/marks").status_code == 401 and c.get("/api/leaders").status_code == 200  # the rest of the site works
    # dev mode opens it (the link on the page); so does a real sender
    monkeypatch.setenv("SKY_AUTH_DEV", "1")
    assert c.get("/api/me").json()["auth"]["email"] is True
    monkeypatch.delenv("SKY_AUTH_DEV")
    accounts.get(st).mail = _Mail()
    assert c.get("/api/me").json()["auth"] == {"email": True, "google": False, "dev": False}


def test_resend_sender_sends_the_link_and_keeps_the_key_out_of_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from service.accounts.mail import LogSender, MailFailedError, ResendSender, sender_from_env

    monkeypatch.delenv("SKY_RESEND_API_KEY", raising=False)
    assert isinstance(sender_from_env(), LogSender)
    monkeypatch.setenv("SKY_RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("SKY_MAIL_FROM", "SPHEREx Space <hello@spherex.space>")
    sender = sender_from_env()
    assert isinstance(sender, ResendSender)
    seen: list[httpx.Request] = []
    status = [200]

    def resend(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status[0], json={"id": "1"})

    sender.transport = httpx.MockTransport(resend)
    link = "https://www.spherex.space/api/auth/callback?token=abc&x=1"
    sender.send_link("ann@example.org", link, 15)
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.resend.com/emails" and req.headers["authorization"] == "Bearer re_test_key"
    assert body["from"] == "SPHEREx Space <hello@spherex.space>" and body["to"] == ["ann@example.org"]
    assert link in body["text"] and "token=abc&amp;x=1" in body["html"] and "15 minutes" in body["text"]
    status[0] = 422
    with caplog.at_level("WARNING"), pytest.raises(MailFailedError):
        sender.send_link("ann@example.org", link, 15)
    assert "re_test_key" not in caplog.text and "ann@example.org" not in caplog.text and "token=abc" not in caplog.text
    # through the route: a mail that could not be sent is said, and the service opens e-mail sign-in with this sender
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    st = _State(tmp_path)
    app = FastAPI()
    register_accounts(app, st)
    mail = accounts.get(st).mail
    assert isinstance(mail, ResendSender)
    mail.transport = httpx.MockTransport(resend)
    c = _client(app)
    assert c.get("/api/me").json()["auth"]["email"] is True
    assert c.post("/api/auth/email", json={"email": "ann@example.org"}).status_code == 502
    status[0] = 200
    assert c.post("/api/auth/email", json={"email": "ann@example.org"}).json() == {"sent": True, "minutes": 15}
