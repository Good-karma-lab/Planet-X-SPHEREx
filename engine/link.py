# ruff: noqa
# mypy: ignore-errors
"""Linking the candidates of several passes into one body: the "jump" (Find Planet X: link of passes).

Between two passes (half a year) the telescope is on the other side of the Sun and a distant body jumps by twice its parallax: 72 arcmin at 95 au, 14 arcmin at 500 au. Two candidates of two passes
belong together if ONE heliocentric orbit passes through both tracks; the fit gives the distance. A third pass is the test: the orbit of two passes predicts where the body is in the third.

Every candidate contributes three points of its track (the first, middle and last visit of its pass: t0 - span/2, t0, t0 + span/2). Free parameters: the two end points of the whole arc (4 numbers),
the distance at the first point and its rate of change. The orbit must be bound (a penalty above 0.9 of the escape speed): two passes do not fix the motion along the line of sight.
Nothing here is a discovery: the caller says what the candidates are and how strong."""

import numpy as np
from scipy.optimize import least_squares

from . import orbit
from . import distant as edist

TOL, SIGMA = 6.0, 2.5  # arcsec: the misfit that is accepted as one body; the position error of a track point
Q0S = (1 / 40, 1 / 60, 1 / 90, 1 / 140, 1 / 220, 1 / 350, 1 / 550, 1 / 900)  # starting 1/distance (1/au) of the fits


def candidate_points(c, grid, t0, span, step=edist.S):
    """The three track points of a candidate of a one-pass result: [(mjd, R km, ra, dec)]. `c` holds x, y (grid pixels of the result's grid), d (au), mu (arcsec per day on the grid's tangent plane);
    `grid` the result's grid (ra0, dec0, n); t0 the reference time of the pass and `span` its length in days. The observer is the Earth's centre (the spacecraft's 7000 km are 0.5 arcsec at 95 au)."""
    T = edist.Tangent(grid["ra0"], grid["dec0"])
    N = grid["n"]
    u0, v0 = (c["x"] - N / 2) * step, (c["y"] - N / 2) * step
    R0 = orbit.earth(t0)
    pts = []
    for tm in (t0 - span / 2.0, t0, t0 + span / 2.0):
        Rm = orbit.earth(tm)
        pu, pv = edist.parallax(c["d"], Rm[None], R0, T)
        ra, dec = T.uv2sky(u0 + pu[0] + c["mu"][0] * (tm - t0), v0 + pv[0] + c["mu"][1] * (tm - t0))
        pts.append((float(tm), Rm, float(ra), float(dec)))
    return pts


def fit(pts, q0s=Q0S):
    """One orbit through the points [(mjd, R, ra, dec)] -> dict(rms (arcsec), dist (au at the first point), dist_err, ddot (km/s), a (au), e, state, plane, t, n) or None when nothing converges."""
    pts = sorted(pts, key=lambda p: p[0])
    t = np.array([p[0] for p in pts])
    R = np.array([p[1] for p in pts])
    ra = np.array([p[2] for p in pts])
    dec = np.array([p[3] for p in pts])
    P = orbit.Plane(float(ra[0]), float(dec[0]))
    u = -((ra - P.rac + 180) % 360 - 180) * P.cd * 3600
    v = (dec - P.decc) * 3600
    a, b = 0, len(t) - 1

    def model(x):
        st = orbit._state(a, b, u[a] + x[0], v[a] + x[1], u[b] + x[2], v[b] + x[3], x[4], x[5], t, R, P)
        pu, pv = orbit.sky(st, t, R, P)
        return st, pu, pv

    def res(x):
        try:
            st, pu, pv = model(x)
        except Exception:  # noqa
            return np.full(2 * len(t) + 1, 1e4)
        r = np.concatenate([pu - u, pv - v])
        r = np.where(np.isfinite(r), r, 1e4)
        esc = (st[1] @ st[1]) / (2 * orbit.MU / np.linalg.norm(st[0]))
        return np.append(r, 200.0 * max(0.0, esc - 0.9))

    best = None
    for q0 in q0s:
        s = least_squares(
            res,
            [0, 0, 0, 0, q0, 0.0],
            bounds=([-60, -60, -60, -60, 1 / 3000, -8], [60, 60, 60, 60, 1 / 20, 8]),
            x_scale=[1, 1, 1, 1, 1e-4, 0.3],
        )
        if best is None or s.cost < best.cost:
            best = s
    if best is None:
        return None
    r = res(best.x)[:-1]
    rms = float(np.sqrt(np.mean(r[: len(t)] ** 2 + r[len(t) :] ** 2)))
    st = model(best.x)[0]
    aa, ee = orbit.elements(st[0], st[1])
    dof = max(2 * len(t) - 6, 1)
    s2 = max(SIGMA**2, 2 * best.cost / dof)
    try:
        cov = s2 * np.linalg.inv(best.jac.T @ best.jac)
        eq = float(np.sqrt(cov[4, 4]))
    except Exception:  # noqa
        eq = float("nan")
    return dict(
        rms=rms,
        dist=float(1 / best.x[4]),
        dist_err=float(eq / best.x[4] ** 2),
        ddot=float(best.x[5]),
        a=float(aa / orbit.AU),
        e=float(ee),
        state=st,
        plane=(P.rac, P.decc),
        t=t,
        n=len(t),
    )


def predict(f, mjd, R):
    """where the fitted orbit is seen at time mjd from R -> ra, dec"""
    P = orbit.Plane(*f["plane"])
    pu, pv = orbit.sky(f["state"], np.array([mjd]), np.asarray(R)[None], P)
    return float(P.rac - pu[0] / 3600 / P.cd), float(P.decc + pv[0] / 3600)


def sep(ra1, dec1, ra2, dec2):
    return float(np.hypot((ra1 - ra2) * np.cos(np.radians(dec1)), dec1 - dec2) * 3600)


def link_passes(passes, tol=TOL, third_max=120.0, progress=None):
    """passes: list (two or three, in time order) of lists of candidates, each candidate = (info dict, points [(mjd, R, ra, dec) x 3]). Every candidate of the first pass is tried with every candidate of the
    second; a link that is one body (misfit <= tol, bound orbit) is tested on the third pass: the predicted place against the nearest candidate (<= third_max arcsec), then all three together.
    -> list of dicts (a, b, fit2, ok, and for three passes: c, pred_sep, pred (ra, dec), fit3, ok3), best first (three passes confirmed, then smaller misfit)."""
    out = []
    pairs = [(ca, cb) for ca in passes[0] for cb in passes[1]]
    for k, ((ia, pa), (ib, pb)) in enumerate(pairs):
        if progress:
            progress(k, len(pairs))
        f = fit(pa + pb)
        if f is None:
            continue
        ok = f["rms"] <= tol and 0 < f["e"] < 1 and f["a"] > 0
        link = dict(a=ia, b=ib, fit2={k_: v for k_, v in f.items() if k_ not in ("state", "t")}, ok=bool(ok))
        if ok and len(passes) > 2:
            best = None
            for ic, pc in passes[2]:
                tm, Rm, ram, decm = pc[1]
                pr = predict(f, tm, Rm)
                d = sep(pr[0], pr[1], ram, decm)
                if best is None or d < best[0]:
                    best = (d, ic, pc, pr)
            if best is not None:
                f3 = fit(pa + pb + best[2])
                if f3 is not None:
                    ok3 = best[0] <= third_max and f3["rms"] <= tol
                    link.update(
                        pred_sep=float(best[0]),
                        c=best[1],
                        pred=[float(best[3][0]), float(best[3][1])],
                        fit3={k_: v for k_, v in f3.items() if k_ not in ("state", "t")},
                        ok3=bool(ok3),
                    )
        out.append(link)
    if progress:
        progress(len(pairs), len(pairs))
    out.sort(key=lambda L: (not L.get("ok3", False), not L["ok"], (L.get("fit3") or L["fit2"])["rms"]))
    return out
