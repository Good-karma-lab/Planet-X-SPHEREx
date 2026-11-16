"""A site for clicking "Check and submit" to the end in a browser: the real accounts routes with a FAKE checks job behind the
contract of GET /api/checks/{job} (product spec §8). Test use only, never part of the product:

    SKY_PRODUCT_DB=/tmp/x/product.sqlite SKY_AUTH_DEV=1 uv run uvicorn tests.fake_checks_site:app --port 8783

The fake job answers `running` for the first few seconds, then `done` with every check passed and one warning (so the page asks
for a note before the submit)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from service.accounts import register_accounts
from service.checks import CHECK_IDS
from service.config import Config

RUN_S = 6.0


class _State:
    def __init__(self) -> None:
        self.cfg = Config(store_dir=Path("store-not-used"))
        self.salt = "fake"
        self.jobs: dict[str, dict[str, Any]] = {}

    def checks_status(self, job: str) -> dict[str, Any] | None:
        j = self.jobs.get(job)
        if j is None:
            return None
        done = time.time() - j["started"] >= RUN_S
        checks = [
            {
                "id": c,
                "state": ("warn" if c == "edge_bright" else "pass") if done else "running",
                "detail": "Fake check: 40″ from a magnitude 8 star." if done and c == "edge_bright" else "",
            }
            for c in CHECK_IDS
        ]
        return {"job": job, **j["place"], "state": "done" if done else "running", "checks": checks, "can_submit": done,
                "needs_comment": ["edge_bright"] if done else []}  # fmt: skip


st = _State()
app = FastAPI()
register_accounts(app, st)


@app.post("/api/checks")
def start(body: dict[str, Any]) -> Any:
    job = f"{len(st.jobs) + 1:020x}"
    st.jobs[job] = {"started": time.time(), "place": {k: body.get(k) for k in ("field_key", "ra", "dec")}}
    return {"job": job, "state": "running"}


@app.get("/api/checks/{job}")
def view(job: str) -> Any:
    found = st.checks_status(job)
    if found is None:
        raise HTTPException(404, "no such job")
    return found
