# ruff: noqa
# mypy: ignore-errors
"""Motion model for linking (the observer comes from the X_SC, Y_SC, Z_SC of the exposures the service has read): a body on a heliocentric orbit seen from a moving observer.

A straight line at a constant rate is wrong over days for two reasons, both of them parallax:
  - the observer is carried by the Earth around the Sun (30 km/s, curved path): over three days the path of a main-belt
    asteroid on the sky bends by 10-25 arcsec;
  - the telescope circles the Earth every 98 minutes, 7000 km from its centre: +-5 arcsec at 2 au, different in every visit.
Both depend on one unknown, the distance.  The model:
    observer(t) = Earth from the Sun (ERFA epv00, built into astropy, no network) + spacecraft from the Earth's centre
                  (X_SC, Y_SC, Z_SC of the IMAGE header, km)
    body(t)     = two-body orbit around the Sun
    sky(t)      = direction of body(t) - observer(t)
Two sky positions and two numbers (distance at the first, distance at the second) fix the orbit; it then predicts where
the body is at any other time.  The distances are not known, so a grid of them is tried (hypotheses()).
Light time is ignored: it shifts the body along its orbit by a nearly constant amount (< 0.3 arcsec of change over days).
"""
import numpy as np, erfa
MU = 1.32712440018e11            # km^3/s^2, Sun
AU = 1.495978707e8               # km
DAY = 86400.0
TT_UTC = 69.184                  # s, TT - UTC since 2017; TDB - TT < 2 ms

def earth(mjd_utc):
    """heliocentric position of the Earth's centre, km, equatorial ICRS axes"""
    pvh, pvb = erfa.epv00(2400000.5, np.asarray(mjd_utc, 'f8') + TT_UTC / DAY)
    return pvh['p'] * AU

def observer(mjds, sc):
    """mean time (MJD) and mean heliocentric position (km) of the telescope over the exposures of a visit: mjds (K), sc (K, 3) the spacecraft from the Earth's centre, km"""
    t = np.asarray(mjds, float)
    p = earth(t) + np.asarray(sc, float)
    return float(t.mean()), p.mean(0)

class Plane:
    """offsets (u, v) in arcsec on the grid of the search <-> unit vectors.  fast.py: ra = RAC - u / 3600 / cos(DECC), dec = DECC + v / 3600"""
    def __init__(s, rac, decc): s.rac, s.decc, s.cd = rac, decc, np.cos(np.radians(decc))
    def vec(s, u, v):
        ra = np.radians(s.rac - np.asarray(u) / 3600 / s.cd); de = np.radians(s.decc + np.asarray(v) / 3600)
        return np.stack([np.cos(de) * np.cos(ra), np.cos(de) * np.sin(ra), np.sin(de)], -1)
    def uv(s, n):
        n = n / np.linalg.norm(n, axis=-1, keepdims=True); ra = np.degrees(np.arctan2(n[..., 1], n[..., 0])); de = np.degrees(np.arcsin(n[..., 2]))
        return -((ra - s.rac + 180) % 360 - 180) * s.cd * 3600, (de - s.decc) * 3600

def _stumpff(z):
    zp = np.maximum(z, 1e-8); zn = np.maximum(-z, 1e-8); sp = np.sqrt(zp); sn = np.minimum(np.sqrt(zn), 50.0)
    C = np.where(z > 1e-6, (1 - np.cos(sp)) / zp, np.where(z < -1e-6, (np.cosh(sn) - 1) / zn, 0.5 - z / 24 + z * z / 720))
    S = np.where(z > 1e-6, (sp - np.sin(sp)) / sp ** 3, np.where(z < -1e-6, (np.sinh(sn) - sn) / sn ** 3, 1 / 6 - z / 120 + z * z / 5040))
    return C, S

def kepler(X0, V0, dt):
    """two-body propagation (universal variable); X0, V0 (..., 3) km, km/s; dt (...) seconds, any sign"""
    r0 = np.linalg.norm(X0, axis=-1); vr = (X0 * V0).sum(-1) / r0; al = 2 / r0 - (V0 * V0).sum(-1) / MU; sm = np.sqrt(MU)
    chi = sm * dt / r0
    for _ in range(8):
        z = al * chi * chi; C, S = _stumpff(z)
        F = r0 * vr / sm * chi * chi * C + (1 - al * r0) * chi ** 3 * S + r0 * chi - sm * dt
        dF = r0 * vr / sm * chi * (1 - z * S) + (1 - al * r0) * chi * chi * C + r0
        chi = chi - F / dF
    z = al * chi * chi; C, S = _stumpff(z)
    f = 1 - chi * chi / r0 * C; g = dt - chi ** 3 / sm * S
    return f[..., None] * X0 + g[..., None] * V0

def two_point(X1, X2, T):
    """velocity at X1 of the orbit that is at X2 after T seconds (short arcs: the straight-line guess is corrected a few times)"""
    V = (X2 - X1) / T[..., None]
    for _ in range(4): V = V + (X2 - kepler(X1, V, T)) / T[..., None]
    return V

def elements(X, V):
    """semi-major axis (km, negative or inf when not bound), eccentricity"""
    r = np.linalg.norm(X, axis=-1); v2 = (V * V).sum(-1); en = v2 / 2 - MU / r; a = np.where(en < 0, -MU / (2 * np.minimum(en, -1e-12)), np.inf)
    h = np.cross(X, V); e = np.sqrt(np.maximum(1 + 2 * en * (h * h).sum(-1) / MU ** 2, 0))
    return a, e

# hypotheses: distance at the first point and the change of distance per second.  The grid is uniform in 1/distance (the
# bending of the path goes as 1/distance); between its nodes the predicted positions are interpolated linearly.
QGRID = np.arange(1 / 6.0, 1 / 0.4 + 1e-9, 0.04)        # 1/au: 6 au ... 0.4 au
DDOT = np.arange(-48.0, 48.1, 4.0)                      # km/s
E_MAX, A_MIN, A_MAX = 0.6, 0.8, 8.0                     # the orbits that are allowed: bound and not extreme (au)
TOL, CLEAN, SIGMA = 5.0, 10.0, 1.5                      # arcsec: linking tolerance, a member farther than CLEAN from the fitted orbit is dropped, position error per coordinate

def grid_predict(n1, R1, t1, n2, R2, t2, Rk, tk, P):
    """orbits through two sky directions (unit vectors n1, n2; observers R1, R2; MJD t1, t2) for every node of the hypothesis grid
    -> predicted u, v (nQ, nD, K) at the K times tk (NaN where the orbit is far from allowed), semi-major axis (au), eccentricity (nQ, nD)"""
    T = (t2 - t1) * DAY; d1 = (AU / QGRID)[:, None] + 0 * DDOT[None, :]; d2 = d1 + DDOT[None, :] * T
    X1 = R1 + d1[..., None] * n1; X2 = R2 + d2[..., None] * n2
    a, e = elements(X1, (X2 - X1) / T); ok = (d2 > 0.02 * AU) & (e < E_MAX + 0.15) & (a > 0.7 * A_MIN * AU) & (a < 1.3 * A_MAX * AU)       # cheap cut on the straight-line velocity
    if not ok.any(): return None
    Xs = X1[ok]; Vs = two_point(Xs, X2[ok], np.full(ok.sum(), T)); a_, e_ = elements(Xs, Vs)
    us, vs = P.uv(kepler(Xs[:, None, :], Vs[:, None, :], ((tk - t1) * DAY)[None, :]) - Rk[None, :, :])
    sh = ok.shape + (len(tk),); pu = np.full(sh, np.nan); pv = np.full(sh, np.nan); A = np.full(ok.shape, np.nan); E = np.full(ok.shape, np.nan)
    pu[ok] = us; pv[ok] = vs; A[ok] = a_ / AU; E[ok] = e_
    return pu, pv, A, E

def _up(A, f, axis):
    """linear interpolation along one axis, f points per grid step; NaN stays NaN"""
    n = A.shape[axis]
    if n < 2 or f < 2: return A
    x = np.arange((n - 1) * f + 1) / f; i0 = np.minimum(x.astype(int), n - 2); w = x - i0; sh = [1] * A.ndim; sh[axis] = -1; w = w.reshape(sh)
    A0 = np.take(A, i0, axis); A1 = np.take(A, i0 + 1, axis)
    return np.where(w == 0, A0, np.where(w == 1, A1, A0 * (1 - w) + A1 * w))

def fine(G, step):
    """the grid cropped to the allowed orbits and interpolated until neighbouring hypotheses differ by less than `step` arcsec on the sky
    -> u, v (H, K), 1/distance (H, 1/au), ddot (H, km/s), index of the 1/distance row (H), a, e (H)"""
    pu, pv, A, E = G; okc = (E < E_MAX) & (A > A_MIN) & (A < A_MAX)
    if not okc.any(): return None
    r = np.where(okc.any(1))[0]; c = np.where(okc.any(0))[0]; r0, r1 = max(r[0] - 1, 0), min(r[-1] + 1, len(QGRID) - 1); c0, c1 = max(c[0] - 1, 0), min(c[-1] + 1, len(DDOT) - 1)
    pu = pu[r0:r1 + 1, c0:c1 + 1]; pv = pv[r0:r1 + 1, c0:c1 + 1]; A = A[r0:r1 + 1, c0:c1 + 1]; E = E[r0:r1 + 1, c0:c1 + 1]
    with np.errstate(all='ignore'):
        s0 = np.nanmax(np.hypot(np.diff(pu, axis=0), np.diff(pv, axis=0))) if pu.shape[0] > 1 else 0.0
        s1 = np.nanmax(np.hypot(np.diff(pu, axis=1), np.diff(pv, axis=1))) if pu.shape[1] > 1 else 0.0
    f0 = int(np.clip(np.ceil(np.nan_to_num(s0) / step), 1, 24)); f1 = int(np.clip(np.ceil(np.nan_to_num(s1) / step), 1, 24))
    Q = QGRID[r0:r1 + 1][:, None] + 0 * DDOT[None, c0:c1 + 1]; Dd = 0 * QGRID[r0:r1 + 1][:, None] + DDOT[None, c0:c1 + 1]
    up = lambda X: _up(_up(X, f0, 0), f1, 1)
    pu, pv, A, E, Q, Dd = [up(X) for X in (pu, pv, A, E, Q, Dd)]; row = np.arange(pu.shape[0])[:, None] + 0 * np.arange(pu.shape[1])[None, :]
    ok = (E < E_MAX) & (A > A_MIN) & (A < A_MAX) & np.isfinite(pu).all(-1)
    return pu[ok], pv[ok], Q[ok], Dd[ok], row[ok], A[ok], E[ok]

def _state(a, b, ua, va, ub, vb, q, ddot, t, R, P):
    """position and velocity (km, km/s) at t[a] of the orbit through two sky places for one hypothesis"""
    T = (t[b] - t[a]) * DAY; d1 = AU / q; X1 = R[a] + d1 * P.vec(ua, va); X2 = R[b] + (d1 + ddot * T) * P.vec(ub, vb)
    return X1, two_point(X1[None], X2[None], np.array([T]))[0], float(t[a])

def sky(state, tk, Rk, P):
    """where the orbit `state` is seen at times tk (K) by observers Rk (K, 3) -> u, v arcsec"""
    X1, V1, t1 = state; return P.uv(kepler(X1[None, :], V1[None, :], (np.atleast_1d(tk) - t1) * DAY) - Rk)

def fit(idx, t, R, u, v, n, P, step=0.5):
    """best allowed orbit through the sources idx (sorted in time).  Anchors: the first and the last; for every hypothesis the
    differences observed - predicted are freed of a straight line in time (that is what an error of the two anchors does),
    what is left is the misfit.  -> dict or None"""
    a, b = idx[0], idx[-1]; G = grid_predict(n[a], R[a], t[a], n[b], R[b], t[b], R[idx], t[idx], P)
    F = fine(G, step) if G is not None else None
    if F is None or not len(F[0]): return None
    pu, pv, Q, Dd, row, A, E = F; tt = t[idx] - t[idx].mean(); w = tt / (tt * tt).sum()
    def detr(r): r = r - r.mean(1, keepdims=True); return r - (r * w).sum(1, keepdims=True) * tt
    ru = detr(u[idx] - pu); rv = detr(v[idx] - pv); chi = (ru * ru + rv * rv).sum(1); h = int(np.argmin(chi))
    prof = np.full(row.max() + 1, np.inf); np.minimum.at(prof, row, chi); qrow = np.full(row.max() + 1, np.nan); qrow[row] = Q
    inside = np.isfinite(prof) & (prof <= chi[h] + SIGMA ** 2)                       # delta chi^2 <= 1 at SIGMA per coordinate
    st = lambda k: _state(a, b, u[a] - ru[k][0], v[a] - rv[k][0], u[b] - ru[k][-1], v[b] - rv[k][-1], Q[k], Dd[k], t, R, P)
    near = np.where(chi <= chi[h] + 4 * SIGMA ** 2)[0]                              # the other orbits that fit about as well: the nearest and the farthest
    return dict(res=np.hypot(ru[h], rv[h]), rms=float(np.sqrt(chi[h] / len(idx))), dist=float(1 / Q[h]), ddot=float(Dd[h]), a=float(A[h]), e=float(E[h]),
                dist_lo=float(1 / np.nanmax(qrow[inside])), dist_hi=float(1 / np.nanmin(qrow[inside])), dist_all=(float(1 / np.nanmax(qrow)), float(1 / np.nanmin(qrow))),
                state=st(h), alts=[st(int(near[np.argmin(Q[near])])), st(int(near[np.argmax(Q[near])]))])

def link(u, v, vis, t, R, P, nmin=3, tol=None, vmin=3.0, vmax=200.0, dt_max=5.0):
    """tracks of at least nmin visits that one allowed heliocentric orbit explains.
    u, v: verified visit sources, arcsec on the grid; vis: their visit; t, R: time and telescope position (prepare)"""
    tol = TOL if tol is None else tol; n = P.vec(u, v); found = set()
    for i in range(len(u)):
        for j in np.where((t > t[i] + 0.01) & (t < t[i] + dt_max) & (vis != vis[i]))[0]:
            rate = np.hypot(u[j] - u[i], v[j] - v[i]) / ((t[j] - t[i]) * 24)
            if not (vmin <= rate <= vmax): continue
            oth = np.where((np.abs(t - t[i]) < dt_max) & (vis != vis[i]) & (vis != vis[j]))[0]
            if not len(oth): continue
            kv, first = np.unique(vis[oth], return_index=True); G = grid_predict(n[i], R[i], t[i], n[j], R[j], t[j], R[oth[first]], t[oth[first]], P)
            if G is None: continue
            # cheap look first: is any source of another visit near the coarse grid of predictions at all?
            pu, pv = G[0], G[1]; fin = np.isfinite(pu[..., 0]); cu = pu[fin]; cv = pv[fin]; col = np.searchsorted(kv, vis[oth])
            with np.errstate(all='ignore'):
                slack = tol + np.nan_to_num(np.nanmax(np.hypot(np.diff(pu, axis=0), np.diff(pv, axis=0)), axis=(0, 1)) if pu.shape[0] > 1 else 0) \
                            + np.nan_to_num(np.nanmax(np.hypot(np.diff(pu, axis=1), np.diff(pv, axis=1)), axis=(0, 1)))
            near = np.hypot(cu[:, col] - u[oth], cv[:, col] - v[oth]).min(0) < slack[col]
            if not near.any(): continue
            F = fine(G, tol / 3)
            if F is None or not len(F[0]): continue
            cand = oth[near]; cc = col[near]; d = np.hypot(F[0][:, cc] - u[cand], F[1][:, cc] - v[cand]); hit = d < tol       # (hypotheses, candidates)
            nv = np.zeros(len(d), int)
            for k in np.unique(cc): nv += hit[:, cc == k].any(1)
            if nv.max() < nmin - 2: continue
            best = np.where(nv == nv.max())[0]; h = best[np.argmin(np.where(hit[best], d[best] ** 2, 0).sum(1))]      # ONE hypothesis must explain all members
            mem = [int(cand[cc == k][np.argmin(d[h, cc == k])]) for k in np.unique(cc) if hit[h, cc == k].any()]
            found.add(frozenset([i, int(j)] + mem))
    groups = []
    for s in sorted(found, key=len, reverse=True):
        for g in groups:
            if len(g & s) >= 2: g |= s; break
        else: groups.append(set(s))
    def clean(m):
        """fit, drop what does not belong (two members of one visit, a member farther than CLEAN), fit again"""
        f = None
        while len(set(vis[m])) >= nmin:
            f = fit(m, t, R, u, v, n, P)
            if f is None:                                   # no allowed orbit through the first and the last member: drop the one farthest from a straight line
                tt = (t[m] - t[m].mean()) * 24; r = np.hypot(u[m] - np.polyval(np.polyfit(tt, u[m], 1), tt), v[m] - np.polyval(np.polyfit(tt, v[m], 1), tt)); m = np.delete(m, int(np.argmax(r))); continue
            dup = [q for q in range(len(m)) if (vis[m] == vis[m[q]]).sum() > 1]
            if dup: m = np.delete(m, max(dup, key=lambda q: f['res'][q])); f = None
            elif f['res'].max() > CLEAN and len(m) > nmin: m = np.delete(m, int(np.argmax(f['res']))); f = None
            else: break
        if f is None or len(set(vis[m])) < nmin or f['rms'] > tol: return None
        tt = (t[m] - t[m].mean()) * 24; pu = np.polyfit(tt, u[m], 1); pv = np.polyfit(tt, v[m], 1)
        f.update(idx=m, nvis=len(set(vis[m])), hours=float(np.ptp(tt)), rate=float(np.hypot(pu[0], pv[0])), pa=float(np.degrees(np.arctan2(-pu[0], pv[0])) % 360), t0=float(t[m].mean()),
                 rms_line=float(np.sqrt(np.mean((u[m] - np.polyval(pu, tt)) ** 2 + (v[m] - np.polyval(pv, tt)) ** 2))))
        return f
    out = [f for f in (clean(np.array(sorted(g, key=lambda q: t[q]))) for g in groups) if f is not None]
    out.sort(key=lambda r: (-r['nvis'], r['rms'])); used = set(); final = []
    for f in out:                                           # a source belongs to one track: the longer track keeps it
        if used & set(f['idx'].tolist()):
            f = clean(np.array([q for q in f['idx'] if q not in used], int))
            if f is None: continue
        used |= set(f['idx'].tolist()); final.append(f)
    return sorted(final, key=lambda r: (-r['nvis'], r['rms']))

def control_trial(u, v, vis, t, R, rac, decc, seed, tol=None):
    """one trial of the control: the verified sources are moved to chance places (mirrored at random and shifted by about 1 arcmin), their times and visits stay -> the tracks
    that chance builds"""
    rng = np.random.default_rng(seed)
    flip = np.where(rng.random(len(u)) < 0.5, -1.0, 1.0)
    u2, v2 = u * flip + rng.normal(0, 60, len(u)), v * flip + rng.normal(0, 60, len(u))
    return [dict(nvis=r["nvis"], rms=r["rms"], hours=r["hours"]) for r in link(u2, v2, vis, t, R, Plane(rac, decc), tol=tol)]
