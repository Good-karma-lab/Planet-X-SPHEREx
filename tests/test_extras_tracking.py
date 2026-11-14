"""Tracked stack (shift-and-add) and injected movers."""

from __future__ import annotations

import numpy as np
import pytest

from engine.extras import injection, tracking

STEP = 3.0  # arcsec per pixel (the film's own grid is 3.075 or more)
N = 64


def noise_frames(f: int = 12, seed: int = 0, n: int = N) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 1.0, (f, n, n)).astype(np.float32), np.linspace(0.0, 11.0, f)  # one frame an hour


def test_the_velocity_grid_is_spaced_by_the_smear_and_bounded_by_the_speed_limit() -> None:
    g = tracking.velocity_grid(span_h=10.0, step_arcsec=6.0, vmax_arcsec_h=12.0, smear_px=1.0)
    assert (0.0, 0.0) in g and max(np.hypot(a, b) for a, b in g) <= 12.0 / 6.0 + 1e-9
    assert len(g) == len({(round(a, 9), round(b, 9)) for a, b in g}) and abs(
        sorted({round(a, 9) for a, _ in g})[1] - sorted({round(a, 9) for a, _ in g})[0]
    ) == pytest.approx(0.1)


def test_a_stack_along_the_true_motion_adds_up_by_the_square_root_of_the_frames() -> None:
    z, t = noise_frames(16)
    m = injection.Mover(32.0, 32.0, 0.8, -0.4, 2.0, float(np.mean(t)))
    zi = injection.inject(z, t, m, fwhm_px=1.5)
    s, n = tracking.stack_along(zi, t, 0.8, -0.4)
    assert int(n[32, 32]) == 16
    # one frame shows 2 sigma; 16 frames add to 2 * 4 = 8 sigma at the reference place
    assert float(np.nanmax(s)) == pytest.approx(8.0, abs=1.5)
    y, x = np.unravel_index(int(np.nanargmax(s)), s.shape)
    assert abs(int(x) - 32) <= 1 and abs(int(y) - 32) <= 1
    wrong, _ = tracking.stack_along(zi, t, -0.8, 0.4)
    assert float(np.nanmax(wrong)) < 5.5


def test_the_search_finds_a_mover_too_faint_for_any_one_frame_and_names_its_velocity() -> None:
    z, t = noise_frames(16, seed=3)
    m = injection.Mover(30.0, 34.0, 0.6, 0.3, 1.8, float(np.mean(t)))
    zi = injection.inject(z, t, m, fwhm_px=1.5)
    assert float(np.max(zi[8])) < 5.5  # not a clear detection in any single frame
    p = tracking.search(zi, t, STEP, vmax_arcsec_h=3.0, top=3)[0]
    assert p.snr > 6.0 and abs(p.x - 30) <= 2 and abs(p.y - 34) <= 2
    assert p.v_col == pytest.approx(0.6, abs=0.12) and p.v_row == pytest.approx(0.3, abs=0.12)
    assert p.v_east == pytest.approx(-0.6 * STEP, abs=0.8) and p.v_north == pytest.approx(-0.3 * STEP, abs=0.8)


def test_pure_noise_does_not_make_a_strong_peak() -> None:
    peaks = []
    for seed in range(6):
        z, t = noise_frames(12, seed=10 + seed)
        peaks.append(tracking.search(z, t, STEP, vmax_arcsec_h=2.0, top=1)[0].snr)
    assert max(peaks) < 7.0


def test_masked_pixels_are_not_counted_and_a_stack_needs_enough_frames() -> None:
    z, t = noise_frames(12)
    z[:, :, :20] = np.nan
    s, n = tracking.stack_along(z, t, 0.0, 0.0)
    assert np.isnan(s[:, :20]).all() and int(n[10, 40]) == 12
    z2, t2 = noise_frames(3)
    assert tracking.search(z2, t2, STEP, 4.0) == []


def test_too_many_velocity_cells_is_refused_with_a_hint() -> None:
    z, t = noise_frames(12)
    with pytest.raises(ValueError, match="velocity cells"):
        tracking.search(z, t, STEP, vmax_arcsec_h=600.0, max_cells=100)


def test_windows_cut_the_frames_into_overlapping_spans() -> None:
    t = np.concatenate([np.linspace(0, 5, 6), np.linspace(30, 35, 6)])
    w = tracking.windows(t, 10.0)
    assert len(w) == 2 and set(w[0]) == set(range(6)) and set(w[1]) == set(range(6, 12))


def test_injection_does_not_unmask_and_makes_the_stated_peak() -> None:
    z, t = noise_frames(4)
    z[:, 10:20, 10:20] = np.nan
    m = injection.Mover(15.0, 15.0, 0.0, 0.0, 10.0, 0.0)
    zi = injection.inject(z, t, m, 2.0)
    assert np.isnan(zi[:, 10:20, 10:20]).all()
    m2 = injection.Mover(40.0, 40.0, 0.0, 0.0, 10.0, 0.0)
    assert float(inject_peak(z, t, m2)) == pytest.approx(10.0, abs=1.5)


def inject_peak(z: np.ndarray, t: np.ndarray, m: injection.Mover) -> float:
    return float(np.nanmax((injection.inject(z, t, m, 2.0) - z)[0]))


def test_magnitude_to_amplitude_follows_the_pogson_scale() -> None:
    assert injection.amplitude_for_mag(18.9) == pytest.approx(4.5)
    assert injection.amplitude_for_mag(19.9) == pytest.approx(4.5 / 10**0.4)


def test_recall_rises_with_brightness_and_the_threshold_is_above_the_noise() -> None:
    z, t = noise_frames(12, seed=5, n=48)
    r = injection.recall_grid(z, t, STEP, amplitudes=[0.3, 5.0], rates_arcsec_h=[3.0], trials=8, seed=2)
    assert r.recall[1][0] >= 0.75 and r.recall[0][0] <= 0.25
    assert r.mean_snr[1][0] > r.mean_snr[0][0] and r.threshold > 3.5 and len(r.noise_peaks) == 8


def _film_part(z: np.ndarray, t_h: np.ndarray, step: float) -> bytes:
    from service.products.sxp import encode

    code = np.where(np.isfinite(z), np.clip(np.round(1 + 254 * np.clip((z + 5.0) / 20.0, 0, 1)), 1, 255), 0).astype(np.uint8)
    meta = {
        "grid": {"step_arcsec": step, "n": z.shape[1]},
        "frames": [{"visit": i, "det": 1, "mjd": 61000.0 + float(t) / 24.0, "exposures": 4} for i, t in enumerate(t_h)],
    }
    return encode({"motion": code, "sky": code}, meta)


def test_film_motion_decodes_the_part() -> None:
    from service.routes_extras import film_motion

    z, t = noise_frames(6, n=16)
    z[0, 0, 0] = np.nan
    z2, th, step = film_motion(_film_part(z, t, 3.075))
    assert np.isnan(z2[0, 0, 0]) and step == 3.075 and th[0] == 0.0 and th[-1] == pytest.approx(11.0, abs=1e-3)
    assert float(np.nanmax(np.abs(z2 - np.clip(z, -5, 15)))) < 0.08  # the 8-bit code of the film


def test_bin2_averages_blocks_and_keeps_all_nan_blocks_nan() -> None:
    from service.routes_extras import bin2

    a = np.ones((1, 4, 4), np.float32)
    a[0, 0, 0] = np.nan
    a[0, 2:4, 2:4] = np.nan
    b = bin2(a)
    assert b.shape == (1, 2, 2) and b[0, 0, 0] == 1.0 and np.isnan(b[0, 1, 1])


def test_the_film_route_finds_an_injected_mover(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from service.products.store import Store
    from service.routes_extras import Extras, register

    z, t = noise_frames(12, seed=4, n=64)
    zi = injection.inject(z, t, injection.Mover(30.0, 34.0, 0.7, 0.3, 3.0, float(np.mean(t))), fwhm_px=1.1)
    store = Store(tmp_path)
    store.begin("film", "abc123", {})
    store.write_part("film", "abc123", "film.sxp", _film_part(zi, t, 3.0))
    store.complete("abc123")
    app = FastAPI()

    class Sky:
        pass

    app.state.sky = Sky()
    app.state.sky.store = store
    register(app, Extras())
    c = TestClient(app)
    r = c.get("/api/v1/film/abc123/tracked", params={"vmax": 3.0, "window_h": 12, "smear": 1.0, "top": 3}).json()
    assert r["state"] == "ok" and r["peaks"][0]["snr"] > 6 and abs(r["peaks"][0]["x"] - 30) <= 3
    assert r["peaks"][0]["v_east_arcsec_h"] == pytest.approx(-0.7 * 3.0, abs=1.0)
    assert c.get("/api/v1/film/zzz/tracked").status_code == 404
    assert c.get("/api/v1/film/a-b/tracked").status_code == 400
    big = c.get("/api/v1/film/abc123/tracked", params={"vmax": 300, "window_h": 72, "smear": 0.5}).json()
    assert big["state"] == "too_many_cells"
    store.close()
