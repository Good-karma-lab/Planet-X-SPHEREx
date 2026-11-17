"""Sharing (product spec §5c): public profile, story, discovery, shared links with their tags and picture, counts."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import accounts
from service.accounts import register_accounts, score, share
from service.config import Config

LOOK = {"seconds": 75, "passes_shown": 4, "passes_total": 4, "local_hour": 14}


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any]:
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    monkeypatch.setenv("SKY_AUTH_DEV", "1")
    monkeypatch.delenv("SKY_PUBLIC_URL", raising=False)
    st: Any = SimpleNamespace(cfg=Config(store_dir=tmp_path), salt="s")
    app = FastAPI()
    register_accounts(app, st)
    return app, st


def _user(app: FastAPI, email: str) -> TestClient:
    c = TestClient(app, base_url="http://localhost", follow_redirects=False)
    link = c.post("/api/auth/email", json={"email": email}).json()["dev_link"]
    c.get(link.replace("http://localhost", ""))
    return c


def _submit(st: Any, c: TestClient, title: str = "A dot that moves") -> str:
    """A submitted finding of this user, through the real routes with a checks job that passed."""
    st.checks_status = lambda job: {"job": job, "state": "done", "can_submit": True, "ra": 269.59, "dec": 4.8, "field_key": "",
                                    "checks": [{"id": "gaia", "state": "pass", "detail": "No Gaia star within 4″."},
                                               {"id": "neowise", "state": "pass", "detail": "NEOWISE has nothing on the path."}]}  # fmt: skip
    mark = {
        "id": f"mark-{title[:6].replace(' ', '')}-0001",
        "field_key": "269.59,+4.8,20",
        "ra": 269.59,
        "dec": 4.8,
        "pass": 0,
        "label": "dot",
        "level": "suspicious",
    }
    c.post("/api/me/marks", json=mark)
    c.post("/api/me/marks", json={**mark, "id": mark["id"] + "b", "pass": 1, "ra": 269.5905})
    inv = c.post(
        "/api/me/investigations", json={"title": title, "mark_ids": [mark["id"], mark["id"] + "b"], "notes": [{"text": "It moves north."}]}
    ).json()
    out = c.post(f"/api/me/investigations/{inv['id']}/submit", json={"checks_job": "j1"}).json()
    assert out["state"] == "submitted"
    fid: str = out["submitted"]
    return fid


def test_profile_is_private_until_its_owner_turns_it_on_and_never_shows_the_email(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    ann = _user(app, "ann.k@example.org")
    guest = TestClient(app, base_url="http://localhost", follow_redirects=False)
    ann.patch("/api/me", json={"name": "Ann K."})
    me = ann.get("/api/me/profile").json()
    assert me == {"slug": "ann-k", "public": False}
    assert guest.get("/api/u/ann-k").status_code == 404  # private: nothing is said
    assert ann.get("/api/u/ann-k").json()["mine"] is True  # she sees her own
    assert guest.get("/s/profile/ann-k").status_code == 404 and guest.get("/s/profile/ann-k/image.png").status_code == 404
    ann.patch("/api/me", json={"public_profile": True})
    p = guest.get("/api/u/ann-k").json()
    assert (p["name"], p["level"], p["points"], p["mine"]) == ("Ann K.", "Observer", 0, False)
    assert [b["id"] for b in p["badges"]] == ["named"]
    assert "example.org" not in str(p) and "email" not in p
    # a second person of the same name gets another address; the first keeps theirs
    bob = _user(app, "bob@example.org")
    bob.patch("/api/me", json={"name": "Ann K."})
    assert bob.get("/api/me/profile").json()["slug"] == "ann-k-2" and ann.get("/api/me/profile").json()["slug"] == "ann-k"
    ann.patch("/api/me", json={"public_profile": False})
    assert guest.get("/api/u/ann-k").status_code == 404


def test_sharing_an_achievement_makes_the_link_the_tags_and_the_picture(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    ann = _user(app, "ann@example.org")
    guest = TestClient(app, base_url="http://localhost", follow_redirects=False)
    ann.patch("/api/me", json={"name": "Ann"})  # earns "Named"
    ann.get("/api/me/score")
    assert guest.post("/api/share", json={"kind": "achievement", "ref": "ann.named", "channel": "x"}).status_code == 401
    assert ann.post("/api/share", json={"kind": "achievement", "ref": "ann.sharp_eye", "channel": "x"}).status_code == 409  # not earned
    out = ann.post("/api/share", json={"kind": "achievement", "ref": "ann.named", "channel": "x"}).json()
    assert out["url"] == "http://localhost/s/achievement/ann.named?c=x" and out["title"] == "Ann earned “Named” on SPHEREx Space"
    assert ann.get("/api/me/profile").json()["public"] is True  # sharing shows the profile: the user clicked for it
    bob = _user(app, "bob@example.org")
    assert bob.post("/api/share", json={"kind": "achievement", "ref": "ann.named", "channel": "x"}).status_code == 403  # not his
    page = guest.get("/s/achievement/ann.named?c=x")
    assert page.status_code == 200
    for tag in (
        '<meta property="og:title" content="Ann earned “Named” on SPHEREx Space">',
        '<meta property="og:image" content="http://localhost/s/achievement/ann.named/image.png">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta http-equiv="refresh" content="0; url=/#/u/ann?badge=named">',
    ):
        assert tag in page.text
    assert "example.org" not in page.text
    crawler = guest.get("/s/achievement/ann.named?c=x", headers={"user-agent": "facebookexternalhit/1.1"})
    assert crawler.status_code == 200
    from PIL import Image

    png = guest.get("/s/achievement/ann.named/image.png")
    assert png.headers["content-type"] == "image/png" and Image.open(io.BytesIO(png.content)).size == (1200, 630)
    counts = guest.get("/api/share/counts").json()["counts"]
    assert counts == [{"kind": "achievement", "ref": "ann.named", "channel": "x", "shares": 1, "visits": 1, "previews": 1}]
    assert set(counts[0]) == {"kind", "ref", "channel", "shares", "visits", "previews"}  # nothing about who


def test_story_is_a_draft_until_published_then_public_with_checks_and_marks(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    ann = _user(app, "ann@example.org")
    guest = TestClient(app, base_url="http://localhost", follow_redirects=False)
    fid = _submit(st, ann)
    assert guest.get(f"/api/stories/{fid}").status_code == 404
    draft = ann.get(f"/api/me/stories/{fid}").json()
    assert draft["public"] is False and draft["title"] == "A dot that moves" and draft["notes"] == "It moves north."
    assert "I marked it on 2 passes" in draft["intro"] and len(draft["marks"]) == 2 and draft["finder"] == "ann"
    assert [c["id"] for c in draft["checks"]] == ["gaia", "neowise"] and draft["status_text"].startswith("It passed every check")
    assert guest.get(f"/api/stories/{fid}").status_code == 404  # a draft is nobody else's
    assert ann.post("/api/share", json={"kind": "story", "ref": fid, "channel": "copy"}).status_code == 409  # publish first
    bob = _user(app, "bob@example.org")
    assert bob.put(f"/api/me/stories/{fid}", json={"title": "Mine now"}).status_code == 404
    out = ann.put(
        f"/api/me/stories/{fid}", json={"title": "A faint dot near Barnard's Star", "intro": "My own words.", "public": True}
    ).json()
    assert out["public"] is True and out["published"]
    pub = guest.get(f"/api/stories/{fid}").json()
    assert (pub["title"], pub["intro"], pub["mine"], pub["discovery"]) == ("A faint dot near Barnard's Star", "My own words.", False, False)
    assert "example.org" not in str(pub)
    link = ann.post("/api/share", json={"kind": "story", "ref": fid, "channel": "telegram"}).json()["url"]
    assert link.startswith("http://localhost/s/story/user%3A") and link.endswith("?c=telegram")
    page = guest.get(link.replace("http://localhost", ""))
    assert f'url=/#/findings/{fid}/story"' in page.text and 'og:title" content="A faint dot near Barnard&#x27;s Star"' in page.text
    assert guest.get(f"/s/story/{fid}/image.png").status_code == 200  # no still here (no clips in this build): the mark and the text
    assert guest.get(f"/s/discovery/{fid}").status_code == 404  # not a discovery
    ann.put(f"/api/me/stories/{fid}", json={"public": False})
    assert guest.get(f"/api/stories/{fid}").status_code == 404 and guest.get(f"/s/story/{fid}").status_code == 404


def test_a_confirmed_finding_is_a_discovery_and_the_latest_one(site: tuple[FastAPI, Any]) -> None:
    app, st = site
    ann, bob = _user(app, "ann@example.org"), _user(app, "bob@example.org")
    guest = TestClient(app, base_url="http://localhost", follow_redirects=False)
    fid = _submit(st, ann)
    assert guest.get("/api/discoveries/latest").json() == {"discovery": None}
    ann.put(f"/api/me/stories/{fid}", json={"public": True})
    acc = accounts.get(st)
    acc.db.credit(fid, bob.get("/api/me").json()["user"]["id"], "checked", "moves")
    acc.db.set_status(fid, "confirmed")
    score.get(st, acc.db).verdict(fid, "confirmed")
    s = guest.get(f"/api/stories/{fid}").json()
    assert s["discovery"] is True and s["finder"] == "ann" and s["checkers"] == ["bob"]
    assert s["summary"].startswith("ann found a new moving object in NASA's SPHEREx infrared survey") and "Checked by bob" in s["summary"]
    latest = guest.get("/api/discoveries/latest").json()["discovery"]
    assert latest["finding_id"] == fid and latest["finder"] == "ann" and latest["page"] == f"#/findings/{fid}/story"
    page = guest.get(f"/s/discovery/{fid}")
    assert 'og:title" content="Discovery: A dot that moves"' in page.text
    # every person credited is told, with the way to the discovery page
    for c in (ann, bob):
        told = [n for n in c.get("/api/me/notifications").json()["notifications"] if "confirmed as a new object" in n["text"]]
        assert len(told) == 1 and told[0]["ref"] == f"#/findings/{fid}/story"
    assert share.slug_of("Ünï cödé!") == "n-c-d" and share.slug_of("***") == "searcher"
