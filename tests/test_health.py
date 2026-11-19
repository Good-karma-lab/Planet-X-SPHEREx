from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from service import __version__
from service.app import create_app
from service.config import Config


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Config(store_dir=tmp_path, web_dist=tmp_path / "none", serve_web=False)))


def test_health_answers(tmp_path: Path) -> None:
    r = client(tmp_path).get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "spherex-sky"
    assert body["version"] == __version__
    assert body["uptime_s"] >= 0


def test_api_answers_are_not_cached_and_carry_security_headers(tmp_path: Path) -> None:
    r = client(tmp_path).get("/api/v1/health")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_unknown_api_path_is_404(tmp_path: Path) -> None:
    assert client(tmp_path).get("/api/v1/nothing-here").status_code == 404


def test_serves_the_built_web_app(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>SPHEREx Sky</title>", encoding="utf-8")
    c = TestClient(create_app(Config(store_dir=tmp_path, web_dist=dist, serve_web=True)))
    r = c.get("/")
    assert r.status_code == 200
    assert "SPHEREx Sky" in r.text
    assert c.get("/api/v1/health").json()["ok"] is True


def test_a_check_of_the_site_does_not_pause_the_sweep_as_a_person_would(tmp_path: Path) -> None:
    """The deployed site's checks send X-Sky-Check: 1: they open fields as a person does, but the sweep goes on."""
    with client(tmp_path) as c:
        assert c.get("/api/v1/health").json()["jobs"]["person_active"] is False
        c.get("/api/v1/fields", headers={"X-Sky-Check": "1"})
        assert c.get("/api/v1/health").json()["jobs"]["person_active"] is False
        c.get("/api/v1/fields")
        assert c.get("/api/v1/health").json()["jobs"]["person_active"] is True
