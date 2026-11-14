from __future__ import annotations

from typing import Any

import numpy as np

from engine import filmsearch as fs
from engine import onoff
from engine.film import Frame

N, STEP = 160, 6.0


def synth_frames(nframes: int = 8, moving: bool = True, seed: int = 3) -> list[Frame]:
    """A film of one detector: a static star in every frame, noise, and (optionally) a body that moves 31 arcsec/hour toward larger x."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:N, 0:N]
    frames = []
    for k in range(nframes):
        t = 61000.0 + 0.06 * k  # one frame every 1.4 hours
        sky = rng.normal(0, 1.0, (N, N)).astype(np.float32) + 5.0
        sky += (200 * np.exp(-((xx - 40) ** 2 + (yy - 40) ** 2) / (2 * 1.5**2))).astype(np.float32)
        motion = rng.normal(0, 1.0, (N, N)).astype(np.float32)
        if moving:
            x = 30.0 + 7.5 * k  # 7.5 grid points of 6 arcsec per 1.44 h = 31 arcsec/hour
            y = 90.0 + 0.5 * k
            blob = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.2**2))
            sky += (30 * blob).astype(np.float32)
            motion += (9 * blob).astype(np.float32)
        frames.append(Frame(visit=k, det=1, mjd=t, n_exposures=4, sky=sky, motion=motion))
    return frames


def test_a_static_star_is_not_a_candidate_and_the_body_is_in_every_frame() -> None:
    frames = synth_frames()
    cands = fs.find_candidates(frames, STEP, N)
    near_star = [c for c in cands if np.hypot(c.x - 40, c.y - 40) < 3]
    assert not near_star  # the star is in the static sky, so the difference does not show it
    body = [c for c in cands if c.sn > 6]
    assert {c.frame for c in body} == set(range(8))


def test_the_body_is_one_straight_track_found_in_every_visit() -> None:
    frames = synth_frames()
    out = fs.search(frames, fs.find_candidates(frames, STEP, N), STEP, N, trials=10)
    assert len(out["tracks"]) == 1
    tr = out["tracks"][0]
    assert tr["method"] == "line" and tr["verified"] is False
    assert tr["n_found"] == 8 and abs(tr["rate"] - 31.25) < 3.0
    assert (
        (tr["pa"] == tr["pa"] and 80 < tr["pa"] < 100) or 260 < tr["pa"] < 280
    )  # east (grid x grows westward: the body moves toward larger x)
    assert all(0 <= p["x"] < N and 0 <= p["y"] < N for p in tr["pts"])
    assert out["control"]["trials"] == 10 and out["control"]["min_visits_drawn"] >= fs.NMIN_FOUND


def test_noise_alone_makes_no_track() -> None:
    frames = synth_frames(moving=False, seed=9)
    out = fs.search(frames, fs.find_candidates(frames, STEP, N), STEP, N, trials=10)
    assert out["tracks"] == []


def test_a_track_is_matched_to_the_known_body_on_its_frames() -> None:
    tr = {"pts": [{"f": k, "x": 30.0 + 7.5 * k, "y": 90.0 + 0.5 * k} for k in range(6)]}
    bodies: list[dict[str, Any]] = [
        {"name": "far", "v": 18.0, "frames": [{"k": k, "x": 100.0, "y": 10.0, "inside": True} for k in range(6)]},
        {
            "name": "9999 Test",
            "v": 18.5,
            "frames": [{"k": k, "x": 30.0 + 7.5 * k + 1.0, "y": 90.0 + 0.5 * k, "inside": True} for k in range(6)],
        },
    ]
    known = fs.match_known([tr], bodies, STEP)
    assert known[0] is not None and known[0]["name"] == "9999 Test" and known[0]["sep_arcsec"] <= 6.5
    assert fs.match_known([tr], bodies[:1], STEP) == [None]


def p(flux: float, lam: float = 1.0, ok: bool = True) -> onoff.Point:
    return onoff.Point(flux, 0.1, lam, ok)


def test_on_minus_off_verifies_light_that_the_references_do_not_show() -> None:
    refs = [p(0.0 + 0.05 * ((k % 3) - 1), 1.0 + 0.01 * k) for k in range(12)]
    on = [p(0.8, 1.0), p(0.9, 1.02), p(0.7, 1.03), p(0.85, 1.05)]
    r = onoff.reduce(on, refs, 3)
    assert r is not None and r["ok"] and r["allpos"] and r["sig"] > 4
    no_light = onoff.reduce([p(0.02), p(-0.05), p(0.03), p(0.0)], refs, 3)
    assert no_light is not None and not no_light["ok"]
    assert onoff.reduce(on, refs[:2], 3) is None  # fewer than four references: no answer, not "no light"
    assert onoff.reduce(on[:2], refs, 3) is None  # too few exposures of the visit
