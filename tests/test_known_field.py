from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import known_field as kf
from service.context import sep_arcsec
from service.routes_known_field import make_known_field_router

# Barnard's Star as Gaia DR3 has it (2016.0) and a faint slow neighbour; a far star outside the frame; one fainter than the limit
GAIA_CSV = (
    "source_id,ra,dec,pmra,pmdec,parallax,phot_g_mean_mag\n"
    "4472832130942575872,269.44850252543836,4.739420051112412,-801.551,10362.394,546.9759,8.19\n"
    "111,269.4600,4.7300,1.0,-2.0,0.5,15.5\n"
    "222,269.9000,4.7300,1.0,-2.0,0.5,12.0\n"
    "333,269.4500,4.7400,0.0,0.0,,20.4\n"
)
SIMBAD = {
    "data": [
        ["NAME Barnard's star", 269.4520769586187, 4.693364966576667, -801.551, 10362.394, 546.9759, "BY*", 9.511, 8.19, 5.244],
        ["LEDA 1", 269.4700, 4.7500, None, None, None, "G", None, None, 14.0],
    ]
}
SB = {
    "fields_second": [
        "Object name",
        "Astrometric RA (hh:mm:ss)",
        "Astrometric Dec (dd mm'ss\")",
        'Dist. from center RA (")',
        'Dist. from center Dec (")',
        'Dist. from center Norm (")',
        "Visual magnitude (V)",
        'RA rate ("/h)',
        'Dec rate ("/h)',
    ],
    "n_second_pass": 1,
    "data_second_pass": [["9078 (1994 PB2)", "17:57:48.00", "+04 44'24.0\"", "0.", "0.", "0.", "18.8", "1.964E+01", "-9.042E+00"]],
}


def sync(fn: Any) -> Any:
    """Run an async test in its own loop."""

    @functools.wraps(fn)
    def run(*a: Any, **kw: Any) -> None:
        asyncio.run(fn(*a, **kw))

    return run


def transport(fail: set[str] | None = None, calls: list[str] | None = None) -> httpx.MockTransport:
    fail = fail or set()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        which = "jpl" if "sb_ident" in url else ("simbad" if "simbad" in url else "gaia")
        if calls is not None:
            calls.append(which)
        if which in fail:
            return httpx.Response(503)
        if which == "jpl":
            return httpx.Response(200, json=SB)
        if which == "simbad":
            return httpx.Response(200, json=SIMBAD)
        return httpx.Response(200, text=GAIA_CSV)

    return httpx.MockTransport(handler)


def make(tmp_path: Path, **kw: Any) -> kf.KnownField:
    return kf.KnownField(tmp_path, client=httpx.AsyncClient(transport=transport(**kw)))


def test_propagate_barnard_moves_ten_arcsec_a_year_and_parallax_is_bounded() -> None:
    ra, dec = np.array([269.4520769586187]), np.array([4.693364966576667])
    pmra, pmdec, plx = np.array([-801.551]), np.array([10362.394]), np.array([546.9759])
    pr, pd = kf.propagate(ra, dec, pmra, pmdec, plx * 0, 2000.0, [2025.0, 2026.0])
    assert sep_arcsec(pr[0, 0], pd[0, 0], pr[0, 1], pd[0, 1]) == pytest.approx(10.393, abs=0.01)
    assert (pd[0, 0] - dec[0]) * 3600 == pytest.approx(25 * 10.362394, abs=0.01)
    # the parallax moves the star by at most its parallax (the Earth is within 1.02 au of the barycentre), and not by zero
    qr, qd = kf.propagate(ra, dec, pmra, pmdec, plx, 2000.0, [2025.0, 2025.25, 2025.5, 2025.75])
    nr, nd = kf.propagate(ra, dec, pmra, pmdec, plx * 0, 2000.0, [2025.0, 2025.25, 2025.5, 2025.75])
    shifts = [sep_arcsec(qr[0, k], qd[0, k], nr[0, k], nd[0, k]) for k in range(4)]
    assert max(shifts) < 0.5469759 * 1.02 and max(shifts) > 0.3


def test_epochs_are_years_or_mjd() -> None:
    assert kf.epoch_mjd(2000.0) == 51544.5
    assert kf.epoch_mjd(61026.5) == 61026.5
    assert kf.epoch_year(51544.5 + 365.25 * 26) == pytest.approx(2026.0)


def test_parse_sexa() -> None:
    assert kf.parse_sexa("11:19:55.39", True) == pytest.approx(169.980792, abs=1e-5)
    assert kf.parse_sexa("+04 24'31.8\"", False) == pytest.approx(4.408833, abs=1e-5)
    assert kf.parse_sexa("-00 30'00.0\"", False) == pytest.approx(-0.5)


@sync
async def test_objects_of_a_frame(tmp_path: Path) -> None:
    k = make(tmp_path)
    out = await k.objects(269.449, 4.741, 6.0, [61026.5, 2026.6])
    assert out["answered"] == {"gaia": True, "simbad": True, "jpl": True}
    by = {o["id"]: o for o in out["objects"]}
    star = by["gaia:4472832130942575872"]
    # the Gaia star carries SIMBAD's name and type; SIMBAD's row of the same star is not listed twice
    assert star["name"] == "Barnard's star" and star["type"] == "BY*" and star["label"] is True
    assert not any(o["source"] == "simbad" and "Barnard" in o["name"] for o in out["objects"])
    assert star["pm_total"] == pytest.approx(10393.3, abs=0.1)
    p0, p1 = star["positions"]
    assert p0["epoch"] == 61026.5 and p1["epoch"] == 2026.6
    assert sep_arcsec(p0["ra"], p0["dec"], p1["ra"], p1["dec"]) == pytest.approx(10.39 * (2026.6 - kf.epoch_year(61026.5)), abs=0.6)
    # the slow faint neighbour: a ring without a label; the far star and the one below the magnitude limit are left out
    assert by["gaia:111"]["label"] is False
    assert "gaia:222" not in by and "gaia:333" not in by
    assert by["simbad:LEDA 1"]["type"] == "G" and by["simbad:LEDA 1"]["mag_band"] == "J"
    body = by["jpl:9078 (1994 PB2)"]
    assert body["label"] is True and body["type"] == "asteroid" and len(body["positions"]) == 2
    assert body["rate_arcsec_h"] == pytest.approx(21.62, abs=0.01)
    await k.client.aclose()


@sync
async def test_a_catalogue_that_fails_is_said_and_not_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_cloud(*a: Any) -> list[dict[str, Any]]:
        raise OSError("no network")

    monkeypatch.setattr(kf, "gaia_cloud_rows", no_cloud)
    k = make(tmp_path, fail={"gaia", "jpl"})
    out = await k.objects(269.449, 4.741, 6.0, [61026.5])
    assert out["answered"] == {"gaia": False, "simbad": True, "jpl": False}
    assert out["status"]["gaia"]["state"] == "unavailable" and out["status"]["jpl"]["state"] == "unavailable"
    assert all(o["source"] == "simbad" for o in out["objects"]) and out["objects"]
    # asked again when the archive is back: the failure was not kept
    k.client = httpx.AsyncClient(transport=transport())
    for b in k.breakers.values():
        b.success()
    out = await k.objects(269.449, 4.741, 6.0, [61026.5])
    assert out["answered"] == {"gaia": True, "simbad": True, "jpl": True}
    await k.client.aclose()


@sync
async def test_gaia_from_the_cloud_copy_when_the_archives_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"source_id": 7, "ra": 269.45, "dec": 4.74, "pmra": 300.0, "pmdec": 0.0, "parallax": 10.0, "phot_g_mean_mag": 20.5}]
    monkeypatch.setattr(kf, "gaia_cloud_rows", lambda *a: rows)
    k = make(tmp_path, fail={"gaia"})
    out = await k.objects(269.449, 4.741, 6.0, [2026.0])
    assert out["answered"]["gaia"] is True and "AWS" in out["status"]["gaia"]["via"]
    assert out["objects"][0]["id"] == "gaia:7" and out["objects"][0]["label"] is True  # faint but fast: kept and named
    await k.client.aclose()


@sync
async def test_kept_on_disk_and_slow_jpl_is_pending(tmp_path: Path) -> None:
    calls: list[str] = []
    k = kf.KnownField(tmp_path, client=httpx.AsyncClient(transport=transport(calls=calls)))
    await k.objects(10.0, 5.0, 6.0, [61026.5])
    n = len(calls)
    k2 = kf.KnownField(tmp_path, client=httpx.AsyncClient(transport=transport(calls=calls)))
    out = await k2.objects(10.0, 5.0, 6.0, [61026.5])
    assert len(calls) == n and out["answered"]["jpl"] is True  # every part came from the disk
    out = await k2.objects(10.0, 5.0, 6.0, [61026.5, 61030.5], jpl_wait_s=0.0)
    assert out["answered"]["jpl"] is False and out["status"]["jpl"]["state"] == "pending" and out["status"]["jpl"]["epochs_answered"] == 1
    assert json.loads(next(tmp_path.glob("known_field/gaia_*.json")).read_text())["state"] == "ok"
    await k.client.aclose()
    await k2.client.aclose()


def test_route(tmp_path: Path) -> None:
    st = SimpleNamespace(cfg=SimpleNamespace(store_dir=tmp_path), known_field=make(tmp_path))
    app = FastAPI()
    app.include_router(make_known_field_router(st))
    with TestClient(app) as c:
        r = c.get("/api/known", params={"ra": 269.449, "dec": 4.741, "size_arcmin": 6, "epochs": "2025.9,2026.6"})
        assert r.status_code == 200 and set(r.json()) >= {"objects", "answered"}
        o = r.json()["objects"][0]
        assert set(o) >= {"id", "source", "name", "type", "mag", "pm_total", "positions", "label"}
        assert r.headers.get("content-encoding") == "gzip"
        small = c.get("/api/known", params={"ra": 269.449, "dec": 4.741, "size_arcmin": 6, "epochs": "2025.9,2026.6", "compact": 1}).json()
        thin = next(x for x in small["objects"] if not x["label"])
        assert set(thin) == {"id", "source", "positions", "mag", "label"} and len(small["objects"]) == len(r.json()["objects"])
        assert c.get("/api/known", params={"ra": 1, "dec": 1, "size_arcmin": 6, "epochs": "x"}).status_code == 400
        assert c.get("/api/known", params={"ra": 1, "dec": 1, "size_arcmin": 600, "epochs": "2026"}).status_code == 422
