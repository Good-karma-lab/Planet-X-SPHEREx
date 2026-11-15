"""The light curves route answers within its deadline: a survey that is slow is `pending`, keeps running, and is found on the next ask."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from service import app as service_app
from service.app import create_app
from service.config import Config
from service.jobs.scheduler import Scheduler
from tests.test_api_fields import setup


def test_a_slow_survey_is_pending_and_answers_on_the_next_ask(tmp_path: Path, monkeypatch: Any) -> None:
    bucket = setup(tmp_path)
    monkeypatch.setattr(service_app, "LIGHTCURVES_WAIT_S", 0.2)
    calls = {"ztf": 0, "neowise": 0}

    async def ztf(params: dict[str, Any]) -> dict[str, Any]:
        calls["ztf"] += 1
        return {"state": "ok", "points": [{"mjd": 59000.0, "mag": 12.0, "err": 0.01, "band": "zr"}]}

    async def neowise(params: dict[str, Any]) -> dict[str, Any]:
        calls["neowise"] += 1
        await asyncio.sleep(1.0)
        return {"state": "none", "points": []}

    monkeypatch.setattr(service_app, "lc_ztf", ztf)
    monkeypatch.setattr(service_app, "lc_neowise", neowise)
    app = create_app(Config(store_dir=tmp_path, web_dist=tmp_path / "none", serve_web=False), scheduler=Scheduler(), s3=bucket.s3())
    with TestClient(app) as c:
        first = c.get("/api/v1/lightcurves", params={"ra": 120.0, "dec": 10.0}).json()
        assert first["ztf"]["state"] == "ok" and first["neowise"]["state"] == "pending"
        again = c.get("/api/v1/lightcurves", params={"ra": 120.0, "dec": 10.0}).json()  # still running: no second request to IRSA
        assert again["neowise"]["state"] == "pending" and calls["neowise"] == 1
        for _ in range(30):
            third = c.get("/api/v1/lightcurves", params={"ra": 120.0, "dec": 10.0}).json()
            if third["neowise"]["state"] != "pending":
                break
            time.sleep(0.2)
        assert third["neowise"]["state"] == "none" and third["ztf"]["state"] == "ok" and calls == {"ztf": 1, "neowise": 1}
