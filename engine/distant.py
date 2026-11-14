# ruff: noqa
# mypy: ignore-errors
"""Search for a slow, distant body of the Solar System in the visits of one pass: the "distance knob" (Find Planet X: one pass).

A body beyond Neptune is too faint for one exposure, and it does not stand still: the telescope rides the Earth around the Sun, so the body slides across the sky by parallax
(the farther, the less), plus its own slow orbital motion. Nothing about any body is passed in. For ONE pass over a field:
  1. visit images on a grid of 3.075 arcsec (every exposure on the grid on its own, smoothed to a point source, the exposures of a visit combined by their median)
  2. static sky = clipped mean of the visit images of the same detector from ALL other passes (the body was elsewhere then); everything the static sky shows is masked, with a margin
     that grows with the brightness of the source; difference = visit - static sky, clipped at +-5 sigma
  3. hypothesis = (distance d, own motion mu): a point at heliocentric distance d on the line of sight is seen from the position of the telescope in every visit (Earth from ERFA
     plus the spacecraft) -> the parallax offsets, plus mu * (t - t0). The differences are shifted by these offsets and added with weights 1/sigma^2
  4. S/N of the sum for every hypothesis, scaled by the robust spread of the sum itself (the noise of the static sky is common to the visits of a detector, so the formal error is too
     low); kept per sky point: the best S/N and its hypothesis. The same for the negated differences gives the height that noise alone reaches (the null); candidates are the peaks
     above it (1.05 times the best negated peak)
The photometry of each candidate on the exposures themselves is done by the service (on - off along the track, control tracks beside it)."""
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.ndimage import binary_dilation, maximum_filter

from . import orbit

S = 3.075                               # grid step, arcsec (half a SPHEREx pixel)
CLIP, STEP, SN_MIN, STAR_SIG = 5.0, 3.0, 5.0, 4.0
ARC = 206264.806
# a static source brighter than `sigma` is masked together with a margin of `points` grid points around its contour (the wings of a bright star reach far)
STAR_TIERS = ((STAR_SIG, 2), (15, 3), (60, 4), (250, 6), (1000, 8), (4000, 10))


class Tangent:
    """gnomonic projection about the field centre; u to the west (east is left on the pictures), v to the north, arcsec"""

    def __init__(self, ra, dec):
        a, d = np.radians(ra), np.radians(dec)
        self.ra, self.dec = ra, dec
        self.n0 = np.array([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)])
        self.e = np.array([-np.sin(a), np.cos(a), 0.0])
        self.n = np.array([-np.sin(d) * np.cos(a), -np.sin(d) * np.sin(a), np.cos(d)])

    def vec2uv(self, v):
        c = v @ self.n0
        return -(v @ self.e) / c * ARC, (v @ self.n) / c * ARC

    def sky2uv(self, ra, dec):
        a, d = np.radians(ra), np.radians(dec)
        return self.vec2uv(np.stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)], -1))

    def uv2sky(self, u, v):
        w = self.n0 + (-np.asarray(u) / ARC)[..., None] * self.e + (np.asarray(v) / ARC)[..., None] * self.n
        w = w / np.linalg.norm(w, axis=-1, keepdims=True)
        return np.degrees(np.arctan2(w[..., 1], w[..., 0])) % 360, np.degrees(np.arcsin(w[..., 2]))


def parallax(d_au, Rv, R0, T):
    """where a point at heliocentric distance d_au, seen at the field centre from R0, is seen from the positions Rv -> u, v arcsec"""
    d = d_au * orbit.AU
    b = R0 @ T.n0
    rho = -b + np.sqrt(b * b - R0 @ R0 + d * d)
    X0 = R0 + rho * T.n0
    w = X0 - Rv
    w = w / np.linalg.norm(w, axis=-1, keepdims=True)
    return T.vec2uv(w)


class TooManyHypotheses(ValueError):
    """the grid of (distance, own motion) would not fit in memory or time; raise the nearest distance"""


HMAX = 1_000_000                         # hypotheses of one pass the service will scan: about 13 min and 1.5 GB on this machine (Eris, pass of July 2025, 30-300 au: 3.6e5, 276 s)


def estimate_hypotheses(dmin, dmax, tv, Rv, i0, T, step=STEP, mu_scale=1.0):
    """how many hypotheses the grid has (within a few per cent), without building it"""
    dm = np.sqrt(dmin * dmax)
    pu, pv = parallax(dm, Rv, Rv[i0], T)
    K = max(np.hypot(pu, pv).max() * dm, 1e-6)
    q = np.arange(1 / dmax, 1 / dmin + 1e-12, step / K) if step / K < (1 / dmin - 1 / dmax) else np.array([1 / dm])
    span = np.abs(tv - tv[i0]).max()
    return int(sum(np.pi * (mu_scale * 5016.0 / d ** 1.5 * span / step + 1) ** 2 for d in 1 / q))


def fit_dmin(dmin, dmax, tv, Rv, i0, T, step=STEP, mu_scale=1.0, hmax=HMAX):
    """the smallest nearest distance >= dmin (raised in steps of 5 %) for which the grid fits under hmax; None when even dmax / 1.2 does not fit"""
    d = dmin
    while d <= dmax / 1.2:
        if estimate_hypotheses(d, dmax, tv, Rv, i0, T, step, mu_scale) <= hmax:
            return d
        d *= 1.05
    return None


def hypotheses(dmin, dmax, tv, Rv, i0, T, step=STEP, mu_scale=1.0, hmax=None):
    """grid of (distance, own motion): neighbouring hypotheses differ by at most `step` arcsec in any visit -> array (nh, 3) of d (au), mu_u, mu_v (arcsec/day)"""
    dm = np.sqrt(dmin * dmax)
    pu, pv = parallax(dm, Rv, Rv[i0], T)
    K = max(np.hypot(pu, pv).max() * dm, 1e-6)                                 # arcsec * au: the offsets go as 1/d
    q = np.arange(1 / dmax, 1 / dmin + 1e-12, step / K) if step / K < (1 / dmin - 1 / dmax) else np.array([1 / dm])
    span = np.abs(tv - tv[i0]).max()
    est = int(sum(np.pi * (mu_scale * 5016.0 / d ** 1.5 * span / step + 1) ** 2 for d in 1 / q))
    if hmax is not None and est > hmax:                                        # the count grows as 1/dmin^3: 30 au is about 1e5 hypotheses a pass, 8 au several million
        raise TooManyHypotheses(est)
    H = []
    for d in 1 / q:
        mmax = mu_scale * 5016.0 / d ** 1.5                                    # a bound body cannot move faster than the escape speed
        dm_ = step / span
        n = int(np.ceil(mmax / dm_))
        for a in range(-n, n + 1):
            for b in range(-n, n + 1):
                if np.hypot(a, b) * dm_ <= mmax + 1e-9:
                    H.append((d, a * dm_, b * dm_))
    return np.array(H)


def offsets(H, tv, Rv, i0, T):
    """offsets of the body in every visit for every hypothesis, arcsec: (nh, nv, 2); zero in the reference visit i0"""
    out = np.zeros((len(H), len(tv), 2))
    cache = {}
    for n, (d, mu, mv) in enumerate(H):
        if d not in cache:
            cache[d] = parallax(d, Rv, Rv[i0], T)
        pu, pv = cache[d]
        out[n, :, 0] = pu + mu * (tv - tv[i0])
        out[n, :, 1] = pv + mv * (tv - tv[i0])
    return out


def static_and_differences(vis, p, tpl=(1, 1, 1, 1, 1, 1), min_good=500):
    """visit - static sky for the visits of pass p. vis: list of dict(k (detector index 0..5), p (pass), t, R, img, n). static sky = clipped mean of the visits of the same detector
    from ALL other passes. -> list of dict(k, t, R, d (clipped), d0, good, sg, s, masked) sorted in time, and a list of notes"""
    out, notes = [], []
    for k in sorted({v["k"] for v in vis}):
        refs = [v["img"] for v in vis if v["k"] == k and v["p"] != p]
        if len(refs) < 2:
            notes.append("D%d: fewer than two visits in the other passes: not used" % (k + 1))
            continue
        st = np.array(refs)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(st, 0)
        dev = st - med
        sgr = 1.4826 * np.nanmedian(np.abs(dev[np.isfinite(dev)]))
        keep = np.isfinite(dev) & (np.abs(dev) <= 3 * sgr)
        cnt = keep.sum(0)
        ref = np.where(cnt >= 2, np.where(keep, st, 0.0).sum(0) / np.maximum(cnt, 1), np.nan)
        fr = np.isfinite(ref)
        mr = np.median(ref[fr])
        sr = 1.4826 * np.median(np.abs(ref[fr] - mr))
        z = np.where(fr, np.abs(ref - mr) / sr, 0.0)
        star = np.zeros(fr.shape, bool)
        for lev, it in STAR_TIERS:
            m = z > lev
            if m.any():
                star |= binary_dilation(m, iterations=it)
        for v in [v for v in vis if v["k"] == k and v["p"] == p]:
            d = v["img"] - ref
            good = np.isfinite(d) & ~star
            if good.sum() < min_good:
                continue
            md = np.median(d[good])
            sg = 1.4826 * np.median(np.abs(d[good] - md))
            d0 = np.where(good, d - md, 0.0).astype("f4")
            if v["n"] < 3:                                                          # no median over a visit: a cosmic ray is still there; far points are left out
                good = good & ~binary_dilation(np.abs(d0) > CLIP * sg, iterations=1)
                d0 = np.where(good, d0, 0.0).astype("f4")
            out.append(dict(k=k, t=v["t"], R=v["R"], d=np.clip(d0, -CLIP * sg, CLIP * sg).astype("f4"), d0=d0, good=good, sg=float(sg), s=float(tpl[k]), masked=float(star.sum() / max(fr.sum(), 1))))
    return sorted(out, key=lambda v: v["t"]), notes


def _stack_block(A, B, sh, wsum):
    """S/N of the shifted sum for a block of hypotheses -> (best S/N per sky point, index in the block, best S/N of the negated data)"""
    nv, N = A.shape[0], A.shape[1]
    best = np.zeros((N, N), "f4")
    arg = np.full((N, N), -1, "i4")
    neg = np.zeros((N, N), "f4")
    num = np.zeros((N, N), "f4")
    den = np.zeros((N, N), "f4")
    for h in range(len(sh)):
        num[:] = 0
        den[:] = 0
        for i in range(nv):
            sx, sy = int(sh[h, i, 0]), int(sh[h, i, 1])                           # sum(x) += A_i(x + shift)
            if abs(sx) >= N or abs(sy) >= N:
                continue
            dy0, dy1 = max(0, -sy), N - max(0, sy)
            dx0, dx1 = max(0, -sx), N - max(0, sx)
            num[dy0:dy1, dx0:dx1] += A[i, dy0 + sy:dy1 + sy, dx0 + sx:dx1 + sx]
            den[dy0:dy1, dx0:dx1] += B[i, dy0 + sy:dy1 + sy, dx0 + sx:dx1 + sx]
        ok = den >= 0.6 * wsum                                                    # at least 60 % of the weight must be there
        sn = np.where(ok, num / np.sqrt(np.maximum(den, 1e-12)), 0.0)
        q = sn[::3, ::3][ok[::3, ::3]]
        if q.size > 200:
            sn /= max(1.4826 * float(np.median(np.abs(q - np.median(q)))), 0.5)    # the sum is scaled by its own robust spread
        m = sn > best
        best[m] = sn[m]
        arg[m] = h
        neg = np.maximum(neg, -sn)
    return best, arg, neg


def search(DV, T, N, dmin, dmax, step=STEP, mu_scale=1.0, workers=6, progress=None, cancel=None, hmax=HMAX):
    """The distance knob on the differences DV of one pass -> dict(H, OFF, tv, Rv, i0, best, arg, neg, w, wa) (best: the best S/N per sky point, arg: its hypothesis, neg: the same for the
    negated data)"""
    tv = np.array([v["t"] for v in DV])
    Rv = np.array([v["R"] for v in DV])
    i0 = int(np.argmin(np.abs(tv - 0.5 * (tv.min() + tv.max()))))
    H = hypotheses(dmin, dmax, tv, Rv, i0, T, step, mu_scale, hmax=hmax)
    OFF = offsets(H, tv, Rv, i0, T)
    SH = np.round(OFF / S).astype("i4")
    w0 = np.array([1 / v["sg"] ** 2 for v in DV])
    sv = np.array([v["s"] for v in DV])
    wa, w = w0 * sv, w0 * sv * sv                                                  # matched filter: sum(wa * d) / sqrt(sum(w))
    A = np.array([v["d"] * wi for v, wi in zip(DV, wa)], "f4")
    B = np.array([v["good"] * wi for v, wi in zip(DV, w)], "f4")
    nch = max(workers, min(len(H), workers * 4))
    cuts = np.linspace(0, len(H), nch + 1).astype(int)
    blocks = [(a, b) for a, b in zip(cuts[:-1], cuts[1:]) if b > a]
    best = np.zeros((N, N), "f4")
    arg = np.full((N, N), -1, "i4")
    neg = np.zeros((N, N), "f4")
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [(a, ex.submit(_stack_block, A, B, SH[a:b], float(w.sum()))) for a, b in blocks]
        for a, f in futs:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("cancelled")
            b_, g_, n_ = f.result()
            m = b_ > best
            best[m] = b_[m]
            arg[m] = a + g_[m]
            neg = np.maximum(neg, n_)
            done += 1
            if progress:
                progress(done, len(blocks), time.time() - t0)
    return dict(H=H, OFF=OFF, tv=tv, Rv=Rv, i0=i0, best=best, arg=arg, neg=neg, w=w, wa=wa, secs=time.time() - t0)


def peaks(M, thr, size=9):
    ys, xs = np.where((M == maximum_filter(M, size)) & (M >= thr))
    o = np.argsort(-M[ys, xs])
    return [(int(xs[k]), int(ys[k]), float(M[ys[k], xs[k]])) for k in o]


def candidates(R, sn_min=SN_MIN, nmax=40, thr=None):
    """peaks of the best-S/N map above the null; a body leaves a ridge of weaker peaks along its track, they are dropped -> (candidates, threshold, the negated peaks)"""
    null = peaks(R["neg"], sn_min)
    out = []
    if thr is None:
        thr = max(sn_min, 1.05 * null[0][2]) if null else sn_min
    for x, y, s in peaks(R["best"], thr):
        h = int(R["arg"][y, x])
        tr = np.stack([x + R["OFF"][h, :, 0] / S, y + R["OFF"][h, :, 1] / S], 1)
        if any(np.hypot(tr[:, 0][:, None] - c["tr"][:, 0][None], tr[:, 1][:, None] - c["tr"][:, 1][None]).min() < 6 for c in out):
            continue
        out.append(dict(x=x, y=y, sn=s, h=h, d=float(R["H"][h, 0]), mu=(float(R["H"][h, 1]), float(R["H"][h, 2])), tr=tr))
        if len(out) >= nmax:
            break
    return out, thr, null


def knob(R, DV, x, y, half=6):
    """S/N at one place as a function of distance: the best over the own motions and over +-half grid points (the "distance knob" curve) -> (distances, S/N)"""
    H, SH = R["H"], np.round(R["OFF"] / S).astype("i4")
    w = R["w"]
    ds = np.unique(H[:, 0])
    out = np.zeros(len(ds))
    pad = int(np.abs(SH).max()) + half + 1
    A = np.array([np.pad(v["d"] * wi, pad) for v, wi in zip(DV, R["wa"])])
    B = np.array([np.pad(v["good"] * wi, pad) for v, wi in zip(DV, w)])
    X, Y = int(round(x)) + pad, int(round(y)) + pad
    for n in range(len(H)):
        num = sum(A[i, Y - half + SH[n, i, 1]:Y + half + 1 + SH[n, i, 1], X - half + SH[n, i, 0]:X + half + 1 + SH[n, i, 0]] for i in range(len(DV)))
        den = sum(B[i, Y - half + SH[n, i, 1]:Y + half + 1 + SH[n, i, 1], X - half + SH[n, i, 0]:X + half + 1 + SH[n, i, 0]] for i in range(len(DV)))
        s = float(np.where(den >= 0.6 * w.sum(), num / np.sqrt(np.maximum(den, 1e-12)), 0).max())
        j = int(np.searchsorted(ds, H[n, 0]))
        out[j] = max(out[j], s)
    return ds, out


def refine(c, R, DV, N):
    """A bright body is clipped in every visit, so many neighbouring hypotheses give the same sum and the best one is not well defined. Here the place and the own motion are tuned on
    the UNclipped differences (distance kept): the S/N of the sum along the track, visit images read at fractional positions. Without it the place is only known to a grid point
    (3 arcsec) and the photometry along the track loses flux. Updates c in place (x, y, mu, sn_unclipped, refined)."""
    from scipy.ndimage import map_coordinates
    from scipy.optimize import minimize
    dt = R["tv"] - R["tv"][R["i0"]]
    w, wa = R["w"], R["wa"]
    Pu = R["OFF"][c["h"], :, 0] - c["mu"][0] * dt
    Pv = R["OFF"][c["h"], :, 1] - c["mu"][1] * dt
    G = [v["good"].astype("f4") for v in DV]

    def cost(p):
        x, y, mu, mv = p
        s = 0.0
        ws = 0.0
        for i, v in enumerate(DV):
            xi = x + (Pu[i] + mu * dt[i]) / S
            yi = y + (Pv[i] + mv * dt[i]) / S
            if not (1 <= xi < N - 2 and 1 <= yi < N - 2) or map_coordinates(G[i], [[yi], [xi]], order=1)[0] < 0.9:
                continue
            s += wa[i] * map_coordinates(v["d0"], [[yi], [xi]], order=1)[0]
            ws += w[i]
        return -s / np.sqrt(ws) if ws > 0.5 * w.sum() else 0.0
    x0 = np.array([c["x"], c["y"], c["mu"][0], c["mu"][1]], "f8")
    st = STEP / max(np.abs(dt).max(), 1e-6)
    r = minimize(cost, x0, method="Nelder-Mead", options=dict(initial_simplex=[x0, x0 + [1, 0, 0, 0], x0 + [0, 1, 0, 0], x0 + [0, 0, st, 0], x0 + [0, 0, 0, st]], xatol=0.02, fatol=0.01, maxiter=600))
    if np.hypot(r.x[0] - x0[0], r.x[1] - x0[1]) <= 5 and np.hypot(r.x[2] - x0[2], r.x[3] - x0[3]) <= 4 * st and -r.fun > -cost(x0):
        c.update(x=float(r.x[0]), y=float(r.x[1]), mu=(float(r.x[2]), float(r.x[3])), sn_unclipped=float(-r.fun), refined=True)
