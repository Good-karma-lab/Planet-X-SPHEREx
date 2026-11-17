"""`POST /api/checks` and `GET /api/checks/{job}`: the checks before submit (service.checks).

`make_checks_router(st)` returns a router for the app; the state gets `st.checks` on first use.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from service.checks import MAX_MARKS, Checks
from service.routes_known_field import known_field_of


class MarkIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    pass_: int = Field(alias="pass", ge=0, le=200)
    ra: float = Field(ge=0.0, lt=360.0)
    dec: float = Field(ge=-90.0, le=90.0)
    mjd: float | None = Field(default=None, ge=48000.0, le=90000.0)  # the date of the mark when it is not the middle of its pass


class OrbitIn(BaseModel):
    """A candidate's orbit for the NEOWISE archive check: the place at `mjd`, the distance, the own motion on the sky (arcsec/day, u to
    the west, v to the north) and the flux in SPHEREx D4 (mJy)."""

    ra: float = Field(ge=0.0, lt=360.0)
    dec: float = Field(ge=-90.0, le=90.0)
    mjd: float = Field(ge=48000.0, le=90000.0)
    d_au: float = Field(ge=20.0, le=5000.0)
    mu: tuple[float, float]
    flux_mjy: float = Field(default=0.0, ge=0.0, le=1000.0)
    scan: bool = False  # also scan the orbit's uncertainty (distance, radial speed, own motion) against shifted places: minutes more


class ChecksIn(BaseModel):
    field_key: str = Field(default="", max_length=80)
    ra: float = Field(ge=0.0, lt=360.0)
    dec: float = Field(ge=-90.0, le=90.0)
    marks: list[MarkIn] = Field(default_factory=list, max_length=MAX_MARKS)
    orbit: OrbitIn | None = None


def checks_of(st: Any) -> Checks:
    ch: Checks | None = getattr(st, "checks", None)
    if ch is None:
        ch = Checks(st, known_field_of(st))
        st.checks = ch
        with contextlib.suppress(RuntimeError):  # no running loop (a test building the state): nothing to resume
            ch.resume()  # jobs a restart interrupted; the integrator may also call this from the app's start-up
    return ch


def checks_status(st: Any, job: str) -> dict[str, Any] | None:
    """The body of `GET /api/checks/{job}`, or None when there is no such job (what `POST /api/findings/submit` asks through
    `st.checks_status`)."""
    found = checks_of(st).get(job) if re.fullmatch(r"[0-9a-f]{20}", job or "") else None
    return None if found is None else found.view()


def make_checks_router(st: Any, prefix: str = "/api") -> APIRouter:
    r = APIRouter()
    st.checks_status = lambda job: checks_status(st, job)  # for POST /api/findings/submit (accounts stream)

    @r.post(f"{prefix}/checks")
    async def start(body: ChecksIn) -> Any:
        """Start (or find) the checks of a set of marks: `{job}`. The same request is the same job; a job with checks that did not
        answer runs them again."""
        if not re.fullmatch(r"[A-Za-z0-9_,.:+-]*", body.field_key):  # a store key, the lab's "ra,dec,fov", or a finding id
            raise HTTPException(400, "bad field_key")
        request: dict[str, Any] = {
            "field_key": body.field_key,
            "ra": round(body.ra, 6),
            "dec": round(body.dec, 6),
            "marks": [
                {"pass": m.pass_, "ra": round(m.ra, 6), "dec": round(m.dec, 6), **({} if m.mjd is None else {"mjd": round(m.mjd, 5)})}
                for m in body.marks
            ],
        }
        if body.orbit is not None:
            request["orbit"] = {**body.orbit.model_dump(), "mu": list(body.orbit.mu)}
        job = checks_of(st).start(request)
        return {"job": job.id, "state": job.state}

    @r.get(f"{prefix}/checks/{{job}}")
    async def view(job: str) -> Any:
        found = checks_status(st, job)
        if found is None:
            raise HTTPException(404, "no such job")
        return found

    return r
