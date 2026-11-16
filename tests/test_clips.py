"""The time-lapse clip of a finding: the picture law, the still background, the ring on the mover, the choice of the field, the routes."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from engine.grid import grid_for_fov
from service.clips import SIZE, Clips, ClipUnavailable, Source, date_label, pass_look, pass_picture, place_at, render
from service.findings import FindingsDB
from service.products.store import Store
from service.products.sxp import encode
from service.routes_clips import make_clips_router
from service.routes_findings import make_findings_router

RA0, DEC0 = 269.45, 4.70
MJD = [60900.0, 61080.0, 61260.0]
MOTION_ARCSEC = 6.0  # northward, per pass


def _blob(n: int, x: float, y: float, amp: float, fwhm: float = 4.0) -> np.ndarray:
    yy, xx = np.mgrid[0:n, 0:n]
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * (fwhm / 2.355) ** 2))


def _field(store: Store, key: str, fov: float, saturated: bool = False) -> dict[str, Any]:
    """A field of three passes: the same 40 stars in each, and one source at the centre that moves north by 6″ a pass."""
    g = grid_for_fov(RA0, DEC0, fov)
    rng = np.random.default_rng(3)
    stars = [(rng.uniform(4, g.n - 4), rng.uniform(4, g.n - 4), rng.uniform(2, 30)) for _ in range(40)]
    params = {"ra": RA0, "dec": DEC0, "fov": fov}
    store.begin("field", key, params)
    passes = []
    for k, mjd in enumerate(MJD):
        base = sum(_blob(g.n, x, y, a) for x, y, a in stars)
        mx, my = (float(v) for v in g.sky_to_grid(RA0, DEC0 + (k - 1) * MOTION_ARCSEC / 3600.0))
        base = base + _blob(g.n, mx, my, 12.0)
        noise = np.random.default_rng(10 + k).normal(0.0, 0.02, (3, g.n, g.n))
        rgb = np.stack([0.8 * base, base, 0.9 * base]) + noise + 0.05
        sat = np.zeros((g.n, g.n), np.uint8)
        if saturated:  # a saturated star: its core has no value and is flagged
            cx, cy = g.n // 4, g.n // 4
            core = _blob(g.n, cx, cy, 1.0, 10.0) > 0.5
            rgb[:, core] = np.nan
            sat[core] = 255
        store.write_part("field", key, f"picture_p{k}.sxp", encode({"rgb": rgb.astype(np.float16), "sat": sat}, {"pass": k}))
        passes.append({"index": k, "mjd0": mjd - 20, "mjd1": mjd + 20, "mjd_mid": mjd, "white_balance": {"kr": 1.25, "kb": 1.1}})
    meta = {
        "kind": "field",
        "params": params,
        "grid": {"ra0": g.ra0, "dec0": g.dec0, "step_arcsec": g.step_arcsec, "n": g.n},
        "passes": passes,
    }
    store.write_meta("field", key, meta)
    store.complete(key)
    return meta


def _mover() -> dict[str, Any]:
    dec = [DEC0 + (k - 1) * MOTION_ARCSEC / 3600.0 for k in range(3)]
    return {"id": "mover:abc", "kind": "mover", "ra": RA0, "dec": DEC0, "data": {"track": {"mjd": MJD, "ra": [RA0] * 3, "dec": dec}}}


def _frames(data: bytes) -> list[np.ndarray]:
    im = Image.open(io.BytesIO(data))
    out = []
    for i in range(im.n_frames):
        im.seek(i)
        out.append(np.asarray(im.convert("RGB"), np.float64))
    return out


def test_the_mover_moves_and_the_background_stands_still(tmp_path: Path) -> None:
    store = Store(tmp_path)
    meta = _field(store, "k" * 24, 5.0)
    data, info = render(_mover(), Source(store, "k" * 24, meta, False))
    fr = _frames(data)
    assert len(fr) == 3 and fr[0].shape == (SIZE, SIZE, 3) and info["bytes"] == len(data)
    assert info["frames"][0]["label"] == date_label(MJD[0] - 20, MJD[0] + 20)
    # the ring follows the object north (up) by 6″ a pass
    decs = [f["dec"] for f in info["frames"]]
    assert np.allclose(np.diff(decs) * 3600.0, MOTION_ARCSEC, atol=0.05)
    # the mover's light is at a different place in each frame: the middle column near the centre changes between the first and last frame
    c = SIZE // 2
    scale = SIZE / (3.0 * 60.0)  # pixels per arcsecond

    def row(
        f: np.ndarray,
    ) -> float:  # the brightness centroid along the middle column (the ring is centred on the object, so it does not shift it)
        v = f[c - 50 : c + 50, c - 3 : c + 4].mean(axis=(1, 2))
        return float((v * np.arange(v.size)).sum() / v.sum())

    bare = _frames(render(_mover(), Source(store, "k" * 24, meta, False), with_ring=False)[0])  # no ring, no arrow: the light alone
    assert abs(row(bare[0]) - row(bare[2]) - 2 * MOTION_ARCSEC * scale) <= 3.0
    # the motion is written on the picture and the arrow points the way it goes (north)
    from service.clips import motion_of

    text, pa = motion_of(_mover()) or ("", None)
    assert (
        text.endswith('"/yr')
        and abs(float(text.split('"')[0]) - MOTION_ARCSEC / (180.0 / 365.25)) < 0.05
        and pa is not None
        and min(pa, 360 - pa) < 1.0
    )
    # the background stands still: away from the mover, the ring and the labels the frames differ only by noise and compression
    quiet = np.ones((SIZE, SIZE), bool)
    quiet[c - 60 : c + 60, c - 60 : c + 60] = False
    quiet[SIZE - 30 :, :] = False
    assert float(np.abs(fr[0] - fr[2])[quiet].mean()) < 6.0
    assert float(np.abs(fr[0] - fr[2])[c - 30 : c + 30, c - 6 : c + 6].mean()) > 12.0


def test_a_saturated_core_is_white_never_black(tmp_path: Path) -> None:
    store = Store(tmp_path)
    meta = _field(store, "s" * 24, 5.0, saturated=True)
    g = grid_for_fov(RA0, DEC0, 5.0)
    from service.products.sxp import decode

    _, arr = decode(store.read_part("field", "s" * 24, "picture_p0.sxp") or b"")
    rgb, sat = arr["rgb"].astype(np.float32), arr["sat"]
    pic = pass_picture(rgb, sat, pass_look(rgb, sat, {"kr": 1.25, "kb": 1.1}), g.step_arcsec)
    assert pic[g.n // 4, g.n // 4].min() > 0.99  # the core
    assert pic[g.n // 4 - 3 : g.n // 4 + 4, g.n // 4 - 3 : g.n // 4 + 4].min() > 0.9  # no dark pixel inside it
    assert pic.min() >= 0.0 and pic.max() <= 1.0 and float(np.median(pic)) < 0.2  # the sky stays dark
    assert meta["passes"]


def test_the_line_through_the_measured_places_and_a_find_that_stands_still() -> None:
    ra, dec = place_at(_mover(), 61170.0)
    assert abs(ra - RA0) < 1e-9 and abs((dec - DEC0) * 3600.0 - 3.0) < 0.01
    still = {"id": "red:1", "kind": "red", "ra": 10.0, "dec": -5.0, "data": {}}
    assert place_at(still, 61170.0) == (10.0, -5.0)
    # a Planet X peak: its other passes lie far away, so the ring stays at its place at the reference date
    peak = {
        "id": "peak:1",
        "kind": "peak",
        "ra": 26.33,
        "dec": -0.44,
        "data": {"peak": {"by_pass": [{"mjd": 1.0, "ra": 27.5, "dec": 0.0}]}},
    }
    assert place_at(peak, 61170.0) == (26.33, -0.44)


def test_the_finest_field_that_holds_the_window_is_used_and_a_mover_on_the_coarse_grid_wants_its_own(tmp_path: Path) -> None:
    store = Store(tmp_path / "main")
    _field(store, "w" * 24, 60.0)
    clips = Clips(store, tmp_path / "clips")
    mover, red = _mover(), {"id": "red:1", "kind": "red", "ra": RA0 + 0.2, "dec": DEC0, "data": {}}
    src = clips.source_for(mover)
    assert src is not None and src.key == "w" * 24 and clips.wants_own(mover, src)  # 3.075″ grid: too coarse for a slow mover
    assert not clips.wants_own(red, clips.source_for(red))  # a source that stands still is cut from the 60′ field
    _field(store, "f" * 24, 5.0)
    clips._fields = (0.0, [])
    fine = clips.source_for(mover)
    assert fine is not None and fine.key == "f" * 24 and not clips.wants_own(mover, fine)
    assert clips.source_for({"id": "x", "ra": RA0 + 5.0, "dec": DEC0}) is None and clips.wants_own(red, None)
    far = {"id": "red:far", "kind": "red", "ra": RA0 + 5.0, "dec": DEC0, "data": {}}
    import asyncio

    try:
        asyncio.run(clips.get(far))
        raise AssertionError("a place no field covers has no clip")
    except ClipUnavailable:
        assert clips.state("red:far") == "none" and clips.summary(far)["reason"]


def _service(tmp_path: Path) -> tuple[TestClient, Any]:
    store = Store(tmp_path / "store")
    _field(store, "f" * 24, 5.0)
    db = FindingsDB(tmp_path / "store" / "findings.db", writer=True)
    m = _mover()
    row = {"place": "test", "fov": 5.0, "rate": 12.0, "snr": 9.0, "score": 9.0, "chance": 0.0, "scans": []}
    far = {"id": "red:far", "kind": "red", "ra": RA0 + 5.0, "dec": DEC0, "data": {"source": {}}}
    db.write_finds([{**row, **m}, {**row, **far, "rate": None}])
    st = SimpleNamespace(
        store=store,
        cfg=SimpleNamespace(store_dir=tmp_path / "store"),
        findings=SimpleNamespace(db=db),
        index=None,
        scheduler=None,
        files=None,
        wavelengths=None,
        fpx_known=None,
    )
    app = FastAPI()
    app.include_router(make_findings_router(st))
    app.include_router(make_clips_router(st))
    return TestClient(app), st


def test_the_routes_serve_the_clip_once_rendered_and_say_why_there_is_none(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKY_CLIPS", str(tmp_path / "clips"))
    monkeypatch.setenv("SKY_PRODUCT_DB", str(tmp_path / "product.sqlite"))
    client, st = _service(tmp_path)
    before = client.get("/api/findings?lite=1&sort=promise").json()
    card = next(f for f in before["finds"] if f["id"] == "mover:abc")
    assert card["clip"]["state"] == "waiting" and card["clip"]["url"].startswith("/api/findings/mover:abc/clip.webp")
    assert "data" not in card and card["stage"] == "new" and card["viewers"] == 0 and card["credit"]["machine"] is True
    r = client.get(card["clip"]["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp" and "immutable" in r.headers["cache-control"]
    assert len(_frames(r.content)) == 3
    made = st.clips.path("mover:abc").stat().st_mtime_ns
    assert client.get("/api/v1/findings/mover:abc/clip.webp").content == r.content and st.clips.path("mover:abc").stat().st_mtime_ns == made
    info = client.get("/api/findings/mover:abc/clip").json()
    assert info["state"] == "ready" and info["frames"] == 3 and info["shows"]["window_arcmin"] == 3.0
    none = client.get("/api/findings/red:far/clip.webp")
    assert none.status_code == 404 and none.json()["state"] == "none" and none.json()["reason"]
    assert client.get("/api/findings/nope/clip.webp").status_code == 404
    after = client.get("/api/findings?lite=1").json()
    assert {f["id"]: f["clip"]["state"] for f in after["finds"]} == {"mover:abc": "ready", "red:far": "none"}
    # a place (a row of the cabinet): the same picture without a ring, only from a finished field, never a field job
    pl = client.get(f"/api/clips/place.webp?ra={RA0}&dec={DEC0}&size=96")
    assert pl.status_code == 200 and pl.headers["content-type"] == "image/webp" and _frames(pl.content)[0].shape == (96, 96, 3)
    off = client.get(f"/api/clips/place.webp?ra={RA0 + 5}&dec={DEC0}")
    assert off.status_code == 404 and off.json()["state"] == "none" and not st.clips.building
    assert client.get(f"/api/clips/place.webp?ra={RA0}&dec={DEC0}&size=97").status_code == 422
    # the shared store got nothing: the clip is in the clips' own folder
    assert not list((tmp_path / "store").rglob("*.webp")) and list((tmp_path / "clips").rglob("*.webp"))


def test_a_track_inside_one_pass_is_shown_in_the_visits_of_one_detector(tmp_path: Path) -> None:
    from service.clips import Clips, render_film

    store = Store(tmp_path / "main")
    n, step = 200, 6.0
    rng = np.random.default_rng(5)
    stars = sum(_blob(n, rng.uniform(0, n), rng.uniform(0, n), rng.uniform(40, 200), 2.5) for _ in range(60))
    frames_meta, sky = [], []
    for i in range(8):
        det = 1 + i % 2  # the visits alternate between two detectors
        body = _blob(n, 40 + 15 * i, 60 + 10 * i, 180, 2.5)
        sky.append(np.clip(1 + stars + body, 1, 255).astype(np.uint8))
        frames_meta.append({"visit": i, "det": det, "mjd": 61000.0 + 0.1 * i, "exposures": 3})
    sky[2][:20, :20] = 0  # this visit did not cover a corner
    key, params = "v" * 24, {"ra": RA0, "dec": DEC0, "fov": 20.0, "mjd": 61000.4}
    meta = {"kind": "film", "params": params, "grid": {"ra0": RA0, "dec0": DEC0, "step_arcsec": step, "n": n}, "frames": frames_meta}
    store.begin("film", key, params)
    store.write_part("film", key, "film.sxp", encode({"sky": np.stack(sky), "motion": np.zeros((8, n, n), np.uint8)}, meta))
    store.write_meta("film", key, meta)
    store.complete(key)
    pts = [{"f": i, "x": 40.0 + 15 * i, "y": 60.0 + 10 * i, "z": 9.0, "how": "found"} for i in (0, 2, 4, 6, 7)]
    find = {"id": "film:t", "kind": "film", "ra": RA0, "dec": DEC0, "fov": 20.0, "data": {"film": 61000.4, "track": {"pts": pts}}}
    clips = Clips(store, tmp_path / "clips")
    hit = clips.visits_for(find)
    assert hit is not None and hit[0] == key
    data, info = render_film(find, store, key, hit[1])
    fr = _frames(data)
    assert info["detector"] == "D1" and [f["frame"] for f in info["frames"]] == [0, 2, 4, 6] and len(fr) == 4  # one detector: no flicker
    assert all(f["det"] == 1 for f in info["frames"]) and info["window_arcmin"] >= 4.0
    ras = [f["ra"] for f in info["frames"]]
    assert ras == sorted(ras, reverse=True)  # x grows to the west: the ring moves west with the body
    assert clips.visits_for({**find, "data": {**find["data"], "film": 60000.0}}) is None  # another pass: not these visits
