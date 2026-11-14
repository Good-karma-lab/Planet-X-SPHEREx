# ruff: noqa
# mypy: ignore-errors
"""The deep sum of SEVERAL passes along one orbit (Find Planet X: the sum of passes).

`distant` adds the visits of one pass (two weeks). Here the visits of all passes are added along ONE heliocentric orbit, so that a body that is under the noise ceiling of every
single pass can rise above the ceiling of the sum. A hypothesis = (distance d, own motion mu) of a body that stands at a sky point of the middle pass at its middle visit; the speed
along the line of sight is taken as zero. The orbit is carried to every visit of every pass (two-body motion, orbit.kepler) and seen from where the telescope was.
To keep it affordable the sum is made in two levels:
  cell   a coarse own motion (3 arcsec over the length of a pass): it fixes the SHAPE of the track inside every pass, so the visits of each pass are added once per cell
  fine   within the cell the own motion is stepped so that the place in the other passes moves by `step` arcsec; the pass images are shifted to where the orbit puts the body and added;
         S/N = sum / sqrt(sum of weights), scaled by its own spread
The same on the negated data gives the height that noise alone reaches. A hypothesis is tried only if the orbit lands inside the field of every pass. A pass is a dict
(T: distant.Tangent of its field, N, tv, Rv, i0, W, Z: array (2, visits, N, N) of weighted differences and weights)."""
import numpy as np

from . import orbit
from .distant import S, ARC

STEP_X, MIN_PASS = 4.5, 0.4
STEP = 3.0                                    # arcsec: the cell of own motion is this many arcsec over the length of the longest pass


def crop(a, y0, y1, x0, x1):
    """a[..., y0:y1, x0:x1], zeros where the box leaves the array"""
    H, W = a.shape[-2:]
    if y0 >= 0 and x0 >= 0 and y1 <= H and x1 <= W:
        return a[..., y0:y1, x0:x1]
    out = np.zeros(a.shape[:-2] + (y1 - y0, x1 - x0), a.dtype)
    ya, yb, xa, xb = max(y0, 0), min(y1, H), max(x0, 0), min(x1, W)
    if yb > ya and xb > xa:
        out[..., ya - y0:yb - y0, xa - x0:xb - x0] = a[..., ya:yb, xa:xb]
    return out


def local(Z, sh, box):
    """the visits of one pass added along one track shape, for the sky box (y0, y1, x0, x1) of that pass -> sum, sum of weights"""
    y0, y1, x0, x1 = box
    num = np.zeros((y1 - y0, x1 - x0), "f4")
    den = np.zeros((y1 - y0, x1 - x0), "f4")
    for i in range(len(sh)):
        sx, sy = int(sh[i, 0]), int(sh[i, 1])
        num += crop(Z[0, i], y0 + sy, y1 + sy, x0 + sx, x1 + sx)
        den += crop(Z[1, i], y0 + sy, y1 + sy, x0 + sx, x1 + sx)
    return num, den


class Geometry:
    """what the scan needs besides the images: the passes (time order), the middle one r, its middle visit time tr and the telescope there Rr"""

    def __init__(self, P, r=None):
        self.P = P
        self.r = len(P) // 2 if r is None else r
        self.tr = float(P[self.r]["tv"][P[self.r]["i0"]])
        self.Rr = P[self.r]["Rv"][P[self.r]["i0"]]
        self.f = None
        self.dq = None

    def start(self, d, uv):
        """body at heliocentric distance d au seen at (u, v) of the middle pass at its middle visit -> position km, distance from the telescope km"""
        Tr = self.P[self.r]["T"]
        w = Tr.n0 + (-uv[0] / ARC) * Tr.e + (uv[1] / ARC) * Tr.n
        n = w / np.linalg.norm(w)
        D = d * orbit.AU
        b = self.Rr @ n
        rho = -b + np.sqrt(b * b - self.Rr @ self.Rr + D * D)
        return self.Rr + rho * n, rho

    def tracks(self, d, uv, mu, times=None):
        """offsets (m, nv, 2) arcsec in the plane of every pass, for the own motions mu (m, 2) arcsec/day. times: list of (mjd array, telescope positions) per pass instead of the visits"""
        Tr = self.P[self.r]["T"]
        X0, rho = self.start(d, uv)
        mu = np.atleast_2d(mu)
        V = (-mu[:, :1] * Tr.e[None] + mu[:, 1:] * Tr.n[None]) / ARC / 86400.0 * rho
        out = []
        for k, p in enumerate(self.P):
            tv, Rv = (p["tv"], p["Rv"]) if times is None else times[k]
            X = orbit.kepler(X0[None, None], V[:, None], ((tv - self.tr) * 86400.0)[None])
            q = X - Rv[None]
            q = q / np.linalg.norm(q, axis=-1, keepdims=True)
            u, v = p["T"].vec2uv(q)
            out.append(np.stack([u, v], -1))
        return out


def cells(d, cs):
    """coarse own motions for the distance d: all a bound body can have (escape speed)"""
    mmax = 5016.0 / d ** 1.5
    n = int(np.ceil(mmax / cs)) + 1
    a = np.arange(-n, n + 1) * cs
    A, B = np.meshgrid(a, a)
    m = np.hypot(A, B) <= mmax + 0.71 * cs
    return np.stack([A[m], B[m]], 1)


def setup(G, step=STEP_X):
    """steps of the scan from the geometry: cell of own motion cs, the fine step f (arcsec/day), the distance step dq (1/au); -> (cs, f, dq)"""
    cs = STEP / max(p["span"] for p in G.P)
    tau = max(abs(p["tv"][p["i0"]] - G.tr) for p in G.P)
    f = step / tau
    return cs, f, tau


def distances(G, dmin, dmax, step=STEP_X):
    """the distances of the scan, uniform in 1/d; sets G.dq and G.f"""
    cs, f, tau = setup(G, step)
    dm = np.sqrt(dmin * dmax)
    z = lambda d: G.tracks(d, (0.0, 0.0), [0.0, 0.0])
    K = max(np.hypot(*(z(dm)[k][0, p["i0"]] - z(1e6)[k][0, p["i0"]])) for k, p in enumerate(G.P)) * dm
    D = 1 / np.arange(1 / dmax, 1 / dmin + 1e-12, step / K)
    G.dq, G.f = step / K, f
    return D, cs, f


def scan(G, job):
    """all hypotheses of the distances job['d'] for the sky box job['box'] of the middle pass -> best S/N per point with its hypothesis, the same for the negated data; job['count']
    only counts the hypotheses that land inside every field"""
    P, r = G.P, G.r
    y0, y1, x0, x1 = job["box"]
    anc = np.array(job["anchor"], "f8")
    cs, f = job["cs"], job["f"]
    Nr = P[r]["N"]
    count = job.get("count", False)
    shp = (y1 - y0, x1 - x0)
    best, bd, bu, bv, neg, nd, nu, nv = (np.zeros(shp, "f4") for _ in range(8))
    nh = nc = 0
    nf = max(1, int(np.ceil(cs / f)))
    fo = (np.arange(nf) - (nf - 1) / 2) * cs / nf
    ctr = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    h = cs
    for d in job["d"]:
        MU = cells(d, cs)
        if not len(MU):
            continue
        O0 = G.tracks(d, anc, MU)
        land = np.ones(len(MU), bool)
        for k, p in enumerate(P):                                                  # cells whose orbit leaves a field are dropped at once
            if k != r:
                b = (O0[k][:, p["i0"]] - anc) / S + (p["N"] - Nr) / 2 + ctr
                land &= (b[:, 0] >= 0) & (b[:, 0] < p["N"]) & (b[:, 1] >= 0) & (b[:, 1] < p["N"])
        idx = np.where(land)[0]
        nc += len(idx)
        if not len(idx):
            continue
        if count:
            nh += len(idx) * nf * nf
            continue
        MU = MU[idx]
        O0 = [o[idx] for o in O0]
        Ou = G.tracks(d, anc, MU + [h, 0])
        Ov = G.tracks(d, anc, MU + [0, h])
        for c in range(len(MU)):
            cen = [O0[k][c, p["i0"]] for k, p in enumerate(P)]
            J = [np.stack([(Ou[k][c, p["i0"]] - cen[k]) / h, (Ov[k][c, p["i0"]] - cen[k]) / h], 1) for k, p in enumerate(P)]
            base = [(cen[k] - anc) / S + (p["N"] - Nr) / 2 for k, p in enumerate(P)]
            L = []
            for k, p in enumerate(P):
                sh = np.round((O0[k][c] - cen[k]) / S).astype(int)
                if k == r:
                    L.append(local(p["Z"], sh, (y0, y1, x0, x1)) + ((y0, x0),))
                    continue
                m = int(np.ceil(np.abs(J[k]).sum(1).max() * cs / 2 / S)) + 2
                b0 = np.round(base[k]).astype(int)
                box = (y0 + b0[1] - m, y1 + b0[1] + m, x0 + b0[0] - m, x1 + b0[0] + m)
                L.append(local(p["Z"], sh, box) + ((box[0], box[2]),))
            nr, dr, _ = L[r]
            okr = dr >= MIN_PASS * P[r]["W"]
            scale = None
            for du in fo:
                for dv in fo:
                    NUM, DEN, ok = nr.copy(), dr.copy(), okr.copy()
                    for k, p in enumerate(P):
                        if k == r:
                            continue
                        s = np.round(base[k] + J[k] @ np.array([du, dv]) / S).astype(int)
                        oy, ox = L[k][2]
                        dp = crop(L[k][1], y0 + s[1] - oy, y1 + s[1] - oy, x0 + s[0] - ox, x1 + s[0] - ox)
                        NUM += crop(L[k][0], y0 + s[1] - oy, y1 + s[1] - oy, x0 + s[0] - ox, x1 + s[0] - ox)
                        DEN += dp
                        ok &= dp >= MIN_PASS * p["W"]
                    if not ok.any():
                        continue
                    sn = np.where(ok, NUM / np.sqrt(np.maximum(DEN, 1e-12)), 0.0).astype("f4")
                    if scale is None:
                        q = sn[::3, ::3][ok[::3, ::3]]
                        scale = max(1.4826 * float(np.median(np.abs(q - np.median(q)))), 0.5) if q.size > 200 else 1.0
                    sn /= scale
                    nh += 1
                    m = sn > best
                    best[m], bd[m], bu[m], bv[m] = sn[m], d, MU[c, 0] + du, MU[c, 1] + dv
                    m = -sn > neg
                    neg[m], nd[m], nu[m], nv[m] = -sn[m], d, MU[c, 0] + du, MU[c, 1] + dv
    return dict(box=job["box"], best=best, bd=bd, bu=bu, bv=bv, neg=neg, nd=nd, nu=nu, nv=nv, nh=nh, nc=nc)


class Orbit:
    """a hypothesis as a track on the sky: where it is in any exposure"""

    def __init__(self, G, uv, d, mu):
        self.G = G
        self.X0, self.rho = G.start(d, uv)
        Tr = G.P[G.r]["T"]
        self.V = (-mu[0] * Tr.e + mu[1] * Tr.n) / ARC / 86400.0 * self.rho
        self.uv, self.d, self.mu = uv, d, mu

    def sky(self, mjd, R):
        """(ra, dec) in degrees of the body seen from the telescope position R at the time mjd"""
        q = orbit.kepler(self.X0, self.V, np.float64((mjd - self.G.tr) * 86400.0)) - R
        q = q / np.linalg.norm(q)
        return float(np.degrees(np.arctan2(q[1], q[0])) % 360), float(np.degrees(np.arcsin(q[2])))


def refine(G, x, y, d, mu, sign=1):
    """The grid of the sum is 3 arcsec and its hypotheses 4.5 arcsec apart; PSF photometry needs the track to about an arcsecond. Place, distance and own motion are tuned on the visit
    images read at fractional positions (S/N of the sum along the orbit, all passes). sign -1: a peak of the negated data. -> x, y, d, mu, S/N tuned"""
    from scipy.ndimage import map_coordinates
    from scipy.optimize import minimize
    P = G.P
    Nr = P[G.r]["N"]

    def cost(q):
        if q[2] <= 0:
            return 0.0
        O = G.tracks(1 / q[2], ((q[0] - Nr / 2) * S, (q[1] - Nr / 2) * S), [q[3], q[4]])
        s = ws = 0.0
        for k, p in enumerate(P):
            xi = p["N"] / 2 + O[k][0, :, 0] / S
            yi = p["N"] / 2 + O[k][0, :, 1] / S
            wp = 0.0
            for i in np.where((xi >= 1) & (xi < p["N"] - 2) & (yi >= 1) & (yi < p["N"] - 2))[0]:
                g = map_coordinates(p["Z"][1, i], [[yi[i]], [xi[i]]], order=1)[0]
                if g > 0:
                    s += map_coordinates(p["Z"][0, i], [[yi[i]], [xi[i]]], order=1)[0]
                    wp += g
            if wp < MIN_PASS * p["W"]:
                return 0.0
            ws += wp
        return -sign * s / np.sqrt(ws)
    x0 = np.array([x, y, 1 / d, mu[0], mu[1]], "f8")
    st = [1.0, 1.0, G.dq, G.f, G.f]
    c0 = cost(x0)
    r = minimize(cost, x0, method="Nelder-Mead", options=dict(initial_simplex=[x0] + [x0 + np.eye(5)[i] * st[i] for i in range(5)], xatol=0.02, fatol=0.005, maxiter=500))
    if r.fun < c0 and np.hypot(r.x[0] - x, r.x[1] - y) <= 4 and abs(r.x[2] - x0[2]) <= 4 * G.dq and np.hypot(r.x[3] - mu[0], r.x[4] - mu[1]) <= 6 * G.f:
        return float(r.x[0]), float(r.x[1]), float(1 / r.x[2]), (float(r.x[3]), float(r.x[4])), float(-r.fun)
    return x, y, d, mu, float(-c0)


# ---- the scan in worker processes (the loops over the fine steps are Python: threads share one core)
_W = None


def save_geometry(G, path, zpaths):
    """write the passes (images to .npy files, the rest to a pickle) for the worker processes"""
    import pickle
    P = []
    for p, zp in zip(G.P, zpaths):
        np.save(zp, p["Z"])
        P.append({k: v for k, v in p.items() if k != "Z"} | {"zpath": zp})
    pickle.dump(dict(P=P, r=G.r, f=G.f, dq=G.dq), open(path, "wb"))


def init_worker(path):
    import pickle
    global _W
    d = pickle.load(open(path, "rb"))
    for p in d["P"]:
        p["Z"] = np.load(p["zpath"], mmap_mode="r")
    _W = Geometry(d["P"], d["r"])
    _W.f, _W.dq = d["f"], d["dq"]


def scan_job(job):
    return scan(_W, job)
