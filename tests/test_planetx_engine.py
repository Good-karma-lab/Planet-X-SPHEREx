# ruff: noqa: N803, N806, SIM300, RUF059
"""Find Planet X: the engine pieces on synthetic bodies (no exposure is read)."""

from __future__ import annotations

import math

import numpy as np

from engine import deepsum as edeep
from engine import distant as edist
from engine import link as elink
from engine import orbit as eorbit
from indexer.store import Exposure
from service.workers import deepjob, linkjob
from service.workers import planetx as px

T1, T2, T3 = 60876.0, 61049.0, 61238.0
SPAN = 12.0
N = 351


def circular_body(ra: float, dec: float, t0: float, d_au: float) -> tuple[np.ndarray, np.ndarray]:
    """A body at heliocentric distance d_au on the line of sight (ra, dec) seen from the Earth at t0, on a circular prograde ecliptic
    orbit: (position km, velocity km/s)."""
    a, d = math.radians(ra), math.radians(dec)
    u = np.array([math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)])
    R0 = eorbit.earth(t0)
    b = float(R0 @ u)
    rho = -b + math.sqrt(b * b - float(R0 @ R0) + (d_au * eorbit.AU) ** 2)
    X = R0 + rho * u
    eps = math.radians(23.4393)
    pole = np.array([0.0, -math.sin(eps), math.cos(eps)])
    v = np.cross(pole, X)
    v = v / np.linalg.norm(v) * math.sqrt(eorbit.MU / np.linalg.norm(X))
    return X, v


def sky_of(X0: np.ndarray, V0: np.ndarray, t: float, t_ref: float, R: np.ndarray) -> tuple[float, float]:
    X = eorbit.kepler(X0, V0, np.float64((t - t_ref) * 86400.0))
    w = X - R
    w = w / np.linalg.norm(w)
    return float(np.degrees(np.arctan2(w[1], w[0])) % 360), float(np.degrees(np.arcsin(w[2])))


def candidate_of(X0: np.ndarray, V0: np.ndarray, t_ref: float, t0: float, d_au: float) -> tuple[dict, dict]:
    """The one-pass candidate a body would give at the pass t0: a grid centred on the body, the own motion that the one-pass model
    (parallax of a point at d_au plus a constant motion) leaves over the pass."""
    ra, dec = sky_of(X0, V0, t0, t_ref, eorbit.earth(t0))
    grid = {"ra0": ra, "dec0": dec, "n": N, "step": edist.S}
    T = edist.Tangent(ra, dec)
    tm = t0 + SPAN / 2
    Rm = eorbit.earth(tm)
    a, d = sky_of(X0, V0, tm, t_ref, Rm)
    u, v = T.sky2uv(a, d)
    pu, pv = edist.parallax(d_au, Rm[None], eorbit.earth(t0), T)
    mu = [float((u - pu[0]) / (tm - t0)), float((v - pv[0]) / (tm - t0))]
    return grid, {"x": N / 2, "y": N / 2, "d": d_au, "mu": mu}


def test_link_of_three_passes_gives_the_distance() -> None:
    X0, V0 = circular_body(27.378, -0.12, T1, 95.0)
    passes = []
    for t0 in (T1, T2, T3):
        grid, c = candidate_of(X0, V0, T1, t0, float(np.linalg.norm(eorbit.kepler(X0, V0, np.float64((t0 - T1) * 86400.0))) / eorbit.AU))
        pts = elink.candidate_points(c, grid, t0, SPAN)
        passes.append([({"i": 0}, pts)])
    links = elink.link_passes(passes, tol=6.0)
    assert links and links[0]["ok"]
    assert links[0].get("ok3")
    assert abs(links[0]["fit3"]["dist"] - 95.0) < 4.0


def test_link_rejects_another_body() -> None:
    X0, V0 = circular_body(27.378, -0.12, T1, 95.0)
    Y0, W0 = circular_body(28.9, 1.4, T1, 140.0)
    g1, c1 = candidate_of(X0, V0, T1, T1, 95.0)
    d2 = float(np.linalg.norm(eorbit.kepler(Y0, W0, np.float64((T2 - T1) * 86400.0))) / eorbit.AU)
    g2, c2 = candidate_of(Y0, W0, T1, T2, d2)
    p1 = [({"i": 0}, elink.candidate_points(c1, g1, T1, SPAN))]
    p2 = [({"i": 0}, elink.candidate_points(c2, g2, T2, SPAN))]
    links = elink.link_passes([p1, p2], tol=6.0)
    assert not links or not links[0]["ok"]


def test_jump_line_passes_through_the_body_at_its_distance() -> None:
    X0, V0 = circular_body(27.378, -0.12, T1, 95.0)
    grid, c = candidate_of(X0, V0, T1, T1, 95.0)
    # the jump carries the own motion over with no radial speed, so the body must be near the line at its own distance
    c.update(d_lo=60.0, d_hi=140.0)
    r = {"grid": grid, "t0": T1}
    line = linkjob.jump_line(r, c, T2)
    assert len(line) == 40 and line[0]["d"] < line[-1]["d"]
    ra, dec = sky_of(X0, V0, T2, T1, eorbit.earth(T2))
    off = [math.hypot((x["ra"] - ra) * math.cos(math.radians(dec)), x["dec"] - dec) * 60.0 for x in line]
    assert min(off) < 2.0  # arcmin: the radial speed and the curve of the orbit are not known, the line stays within a corridor
    assert line[int(np.argmin(off))]["d"] > 70.0
    # at zero own motion and distance the line is the parallax line: it is long (the Earth moved by ~2 au)
    ras = np.array([x["ra"] for x in line])
    decs = np.array([x["dec"] for x in line])
    assert float(np.hypot(np.ptp(ras) * math.cos(math.radians(dec)), np.ptp(decs))) * 60.0 > 15.0


def test_auto_fields_follow_parallax() -> None:
    fl = deepjob.auto_fields(27.378, -0.12, T1, [T1, T2, T3], 90.0)
    assert [f[2] for f in fl] == [T1, T2, T3]
    assert abs(fl[0][0] - 27.378) < 0.02 and abs(fl[0][1] + 0.12) < 0.02  # the first pass is the place itself
    assert math.hypot(fl[1][0] - fl[0][0], fl[1][1] - fl[0][1]) > 0.5  # the next passes are about a degree away


def test_deep_geometry_counts_orbits_of_the_right_place() -> None:
    X0, V0 = circular_body(27.378, -0.12, T1, 95.0)
    P = []
    for t0 in (T1, T2, T3):
        ra, dec = sky_of(X0, V0, t0, T1, eorbit.earth(t0))
        tv = t0 + np.linspace(-SPAN / 2, SPAN / 2, 14)
        P.append(
            {
                "T": edist.Tangent(ra, dec),
                "N": N,
                "tv": tv,
                "Rv": np.array([eorbit.earth(t) for t in tv]),
                "i0": 7,
                "W": 1.0,
                "span": float(np.abs(tv - tv[7]).max()),
            }
        )
    G = edeep.Geometry(P)
    D, cs, fstep = edeep.distances(G, 80.0, 115.0)
    assert len(D) > 5 and 80.0 <= D.min() and D.max() <= 115.0 + 1e-6
    nh = edeep.scan(G, {"box": (0, N, 0, N), "anchor": (0.0, 0.0), "d": D, "cs": cs, "f": fstep, "count": True})
    assert nh["nh"] > 10_000
    # the orbit of the body itself lands inside every field: the middle pass has the body at its centre at its middle visit
    orb = edeep.Orbit(G, (0.0, 0.0), 95.0, (0.0, 0.0))
    ra, dec = orb.sky(G.tr, G.Rr)
    ra_t, dec_t = P[G.r]["T"].ra, P[G.r]["T"].dec
    assert math.hypot((ra - ra_t) * math.cos(math.radians(dec)), dec - dec_t) * 3600 < 1.0


def _exp(i: int, det: int, mjd: float) -> Exposure:
    return Exposure(id=i, key=f"k{i}", release="dr1", period="p", seq=i, group=i, det=det, mjd0=mjd, mjd1=mjd + 0.001, size=1, md5="x")


def test_cap_keeps_whole_visits_and_the_searched_pass() -> None:
    exps = []
    pno = []
    i = 0
    for q, t0 in enumerate((100.0, 300.0, 500.0)):
        for v in range(400):  # 400 visits of 3 exposures per pass and detector 1
            for e in range(3):
                exps.append(_exp(i, 1, t0 + v * 0.5 + e * 0.001))
                pno.append(q)
                i += 1
    pno_a = np.array(pno)
    keep, kp, note = px.cap_exposures_by_visits(exps, pno_a, 1)
    assert len(keep) <= 1400 + 6 and note
    assert int((kp == 1).sum()) <= 800 + 3
    # whole visits: the kept exposures of a pass come in threes
    for q in range(3):
        assert int((kp == q).sum()) % 3 == 0
    small, kp2, note2 = px.cap_exposures_by_visits(exps[:60], pno_a[:60], 0)
    assert len(small) == 60 and note2 is None


def test_honest_words_carry_the_limits() -> None:
    h = px.honest_words("deep", (1, 2, 3))
    # the depth is the field's own measured bar, never a figure from elsewhere
    assert "bar of the sum of the passes" in h["limit"] and "mJy" not in h["limit"] and "lead" in h
    assert "did not use D4" in h["planet_nine"]
    assert "did not use D4" not in px.honest_words("distant", (1, 2, 3, 4))["planet_nine"]


def test_the_link_reports_what_chance_links() -> None:
    """The real body links across three passes; with its second pass moved to chance places of the field (±N·S/2) no orbit fits
    within 6″, so the chance control says 0 links per trial, and the result carries it next to the real link."""
    X0, V0 = circular_body(27.378, -0.12, T1, 95.0)
    passes = []
    for t0 in (T1, T2, T3):
        grid, c = candidate_of(X0, V0, T1, t0, float(np.linalg.norm(eorbit.kepler(X0, V0, np.float64((t0 - T1) * 86400.0))) / eorbit.AU))
        passes.append([({"i": 0}, elink.candidate_points(c, grid, t0, SPAN))])
    ch = linkjob.chance_links(passes, [N * edist.S / 2.0] * 3, 6.0, trials=4)
    assert ch == {"trials": 4, "pairs": 1, "one_body": ch["one_body"], "confirmed": 0.0}
    assert ch["one_body"] <= 0.25  # a chance place hardly ever fits one orbit with the first pass within 6″
