# ruff: noqa
# mypy: ignore-errors
"""Check of a candidate in the archive of WISE (NEOWISE-R, 2014-2024; the port of motion_pipeline/px/wise.py to the service).

WISE looked at every sky place twice a year, at the same 90 degrees from the Sun as SPHEREx, about a dozen exposures a time, so a body that the sum of SPHEREx passes brings out has already been
in the field of ten years of frames. The check puts the candidate's orbit back to every epoch and adds the epoch differences along it:
  1. every exposure (a cutout of the strip the candidate crosses in those years) is put on one sky grid, in mJy
  2. epoch image = median of the exposures of an epoch (a gap of 30 days cuts epochs); static sky = clipped mean of the other epochs; what the static sky shows is masked (levels in units of the
     noise of ONE epoch: the body is at a new place in every epoch, so the leftovers of different stars do not add up along its track); difference = epoch - static sky, the large-scale background removed
  3. the orbit of the candidate is carried back to every epoch; its uncertainty is scanned (distance, speed along the line of sight, own motion); for every orbit the epoch differences are added along it
  4. the same sums are made for the same orbits shifted on the sky (a box around the candidate): what they reach is what noise alone gives
  5. flux scale: the static sky at the places of catalogued stars (CatWISE W1) against their catalogue flux (`calibrate`)
The result says what a body of the candidate's flux would have given in WISE (`would_be_sigma`): a candidate that WISE should have seen at 13 sigma and did not is ruled out."""

import numpy as np
from scipy.ndimage import gaussian_filter, uniform_filter, binary_dilation

from engine import orbit
from engine import distant as edist

S = edist.S  # grid step, arcsec
ARC = edist.ARC
CLIP = 5.0
HALF_ROWS, BOX = (
    190,
    (30, 60),
)  # the strip: rows kept on each side of the centre; the box of shifted orbits: +-rows, +-columns (grid points)
WIDTH = 1172  # grid points along the strip (1 degree)
SIG_SPLAT, SIG_PS = 0.30 * 6.15, 3.2  # splat kernel and point-source smoothing, arcsec (the one of the distant-body search)
MASK_TIERS = ((10, 2), (40, 3), (150, 5), (600, 8), (2500, 12))
MU_HALF, MU_STEP = 0.04, 0.0007  # arcsec / day around the candidate's own motion; the step
VR = np.linspace(-2.0, 2.0, 9)  # km / s
PSF_W1 = 2.6  # sigma of a W1 point image, arcsec
MAGZP_MJY = 309540.0  # W1 zero magnitude flux, mJy
_W = {}


def ibe_url(row, band=1):
    """the data path of a frame of the NEOWISE-R single-exposure table (IBE search row: scan_id, frame_num) -> path under https://irsa.ipac.caltech.edu/ibe/data/wise/neowiser/p1bm_frm/"""
    scan = str(row["scan_id"]).strip()
    fn = int(row["frame_num"])
    return "%s/%s/%03d/%s%03d-w%d-int-1b.fits" % (scan[-2:], scan, fn, scan, fn, int(band))


def to_grid(e, W, H, y0, sig_splat=SIG_SPLAT, sig_ps=SIG_PS):
    """splat the pixels of one exposure on the strip grid (W columns, H rows; row y0 is the strip's centre line) and smooth to the size of a point source; NaN where there is no data.
    e: dict(u, v (arcsec on the strip's tangent plane), val (mJy-like values, the median taken out), ok)"""
    x = W / 2 + e["u"] / S
    y = y0 + e["v"] / S
    sig = sig_splat / S
    K = int(np.ceil(3 * sig))
    num = np.zeros(W * H)
    den = np.zeros(W * H)
    sel = e["ok"] & (x > -K) & (x < W + K) & (y > -K) & (y < H + K)
    xs, ys, vs = x[sel], y[sel], e["val"][sel]
    ix, iy = np.round(xs).astype(int), np.round(ys).astype(int)
    for dy in range(-K, K + 1):
        for dx in range(-K, K + 1):
            px, py = ix + dx, iy + dy
            o = (px >= 0) & (px < W) & (py >= 0) & (py < H)
            w = np.exp(-((px - xs) ** 2 + (py - ys) ** 2) / (2 * sig**2))[o]
            idx = (py * W + px)[o]
            num += np.bincount(idx, w * vs[o], W * H)
            den += np.bincount(idx, w, W * H)
    okg = (den > 0.3).reshape(H, W)
    im = np.where(okg, (num / np.maximum(den, 1e-9)).reshape(H, W), 0.0)
    sp = sig_ps / S
    w = gaussian_filter(okg.astype("f8"), sp)
    return np.where(okg & (w > 0.7), gaussian_filter(im, sp) / np.maximum(w, 1e-6), np.nan).astype("f4")


def epoch_images(frames, gap=30.0, min_cover=4):
    """frames: list of (mjd, image on the strip grid). Epochs are cut at gaps of `gap` days; the epoch image is the median of the frames that cover a place (at least `min_cover` of them).
    -> list of dict(t, R, img, n)"""
    frames = sorted(frames, key=lambda q: q[0])
    if not frames:
        return []
    t = np.array([q[0] for q in frames])
    cut = np.where(np.diff(t) > gap)[0]
    edges = [0] + list(cut + 1) + [len(frames)]
    vis = []
    for a, b in zip(edges[:-1], edges[1:]):
        st = np.array([q[1] for q in frames[a:b]])
        cnt = np.isfinite(st).sum(0)
        tm = float(t[a:b].mean())
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(st, 0)
        vis.append(dict(t=tm, R=orbit.earth(tm), img=np.where(cnt >= min_cover, med, np.nan).astype("f4"), n=int(b - a)))
    return vis


def differences(vis):
    """epoch - static sky (clipped mean of the other epochs), static sources masked, large-scale background removed, clipped -> list of dict(t, R, d, good, sg, masked)"""
    import warnings

    out = []
    imgs = np.array([v["img"] for v in vis])
    for i, v in enumerate(vis):
        st = np.delete(imgs, i, 0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(st, 0)
        dev = st - med
        if not np.isfinite(dev).any():
            continue
        sgr = 1.4826 * np.nanmedian(np.abs(dev[np.isfinite(dev)]))
        keep = np.isfinite(dev) & (np.abs(dev) <= 3 * sgr)
        cnt = keep.sum(0)
        ref = np.where(cnt >= 2, np.where(keep, st, 0.0).sum(0) / np.maximum(cnt, 1), np.nan)
        d = v["img"] - ref
        fin = np.isfinite(d)
        if fin.sum() < 500:
            continue
        sg = 1.4826 * np.median(np.abs(d[fin] - np.median(d[fin])))
        fr = np.isfinite(ref)
        z = np.where(fr, (ref - np.median(ref[fr])) / sg, 0.0)
        star = np.zeros(fr.shape, bool)
        for lev, it in MASK_TIERS:
            m = z > lev
            if m.any():
                star |= binary_dilation(m, iterations=it)
        good = fin & ~star
        g = good.astype("f4")
        d0 = np.where(good, d, 0.0)
        bg = uniform_filter(d0, 61) / np.maximum(uniform_filter(g, 61), 0.05)
        d0 = np.where(good, d - bg, 0.0)
        sg = 1.4826 * np.median(np.abs(d0[good]))
        out.append(
            dict(
                t=v["t"],
                R=v["R"],
                d=np.clip(d0, -CLIP * sg, CLIP * sg).astype("f4"),
                good=good,
                sg=float(sg),
                masked=float(star.sum() / max(fr.sum(), 1)),
            )
        )
    return out


def state(G, uv, d, mu, vr):
    """position and velocities (m, 3) of a body seen at uv of the search field at its reference time: distance d, own motions mu (m, 2) arcsec/day, radial speed vr km/s.
    G: dict(T (Tangent of the field), Rr (observer at the reference time, km), tr (reference time))"""
    Tr = G["T"]
    w = Tr.n0 + (-uv[0] / ARC) * Tr.e + (uv[1] / ARC) * Tr.n
    n = w / np.linalg.norm(w)
    D = d * orbit.AU
    b = G["Rr"] @ n
    rho = -b + np.sqrt(b * b - G["Rr"] @ G["Rr"] + D * D)
    mu = np.atleast_2d(mu)
    return G["Rr"] + rho * n, (-mu[:, :1] * Tr.e[None] + mu[:, 1:] * Tr.n[None]) / ARC / 86400.0 * rho + vr * n[None]


def places(G, Tw, W, X0, V, tk, Rk):
    """grid positions (m, nk, 2) of the bodies at the epochs on the strip grid (W columns, the centre line at HALF_ROWS)"""
    X = orbit.kepler(X0[None, None], V[:, None], ((tk - G["tr"]) * 86400.0)[None])
    q = X - Rk[None]
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    u, v = Tw.vec2uv(q)
    return np.stack([W / 2 + u / S, HALF_ROWS + v / S], -1)


def init(W):
    _W.clear()
    _W.update(W)


def scan(job):
    """all own motions for one (distance, radial speed): the best S/N per point of the box, with its hypothesis"""
    W = _W
    d, vr = job
    by, bx = BOX
    A, B = W["A"], W["B"]
    nk, H, Wd = A.shape
    mus = W["mus"]
    X0, V = state(W["G"], W["uv"], d, mus, vr)
    P = np.round(places(W["G"], W["Tw"], W["W"], X0, V, W["tk"], W["Rk"])).astype(int)
    best = np.zeros((2 * by + 1, 2 * bx + 1), "f4")
    arg = np.zeros(best.shape, "i4")
    wt = W["wsum"]
    for h in range(len(mus)):
        num = np.zeros(best.shape, "f4")
        den = np.zeros(best.shape, "f4")
        for k in range(nk):
            x, y = P[h, k]
            if y - by < 0 or y + by + 1 > H or x - bx < 0 or x + bx + 1 > Wd:
                continue
            num += A[k, y - by : y + by + 1, x - bx : x + bx + 1]
            den += B[k, y - by : y + by + 1, x - bx : x + bx + 1]
        ok = den >= 0.6 * wt
        sn = np.where(ok, num / np.sqrt(np.maximum(den, 1e-12)), 0.0)
        q = sn[ok]
        if q.size > 500:
            sn = sn / max(1.4826 * float(np.median(np.abs(q - np.median(q)))), 0.5)
        m = sn > best
        best[m] = sn[m]
        arg[m] = h
    return d, vr, best, arg


def calibrate(ref, Tw, stars, W=WIDTH):
    """the grid value of a point source per mJy: the static sky (mean of the epoch images) at the places of isolated catalogued stars of W1 13.5-15.8 against their catalogue flux. stars: list of
    (ra, dec, w1mpro). -> (scale, n stars used)"""
    if not len(stars):
        return float("nan"), 0
    a = np.array([[s[0], s[1], s[2]] for s in stars], float)
    cu, cv = Tw.sky2uv(a[:, 0], a[:, 1])
    cx = W / 2 + cu / S
    cy = HALF_ROWS + cv / S
    cm = a[:, 2]
    cal = []
    for i in range(len(a)):
        if not (13.5 < cm[i] < 15.8 and 12 < cx[i] < W - 13 and 12 < cy[i] < 2 * HALF_ROWS - 13):
            continue
        if (np.hypot(cx - cx[i], cy - cy[i]) * S < 25).sum() > 1:
            continue
        x, y = int(round(cx[i])), int(round(cy[i]))
        pk = ref[y - 1 : y + 2, x - 1 : x + 2]
        bgd = np.nanmedian(ref[y - 12 : y + 13, x - 12 : x + 13])
        if np.isfinite(pk).all():
            cal.append(float(pk.max() - bgd) / (MAGZP_MJY * 10 ** (-0.4 * cm[i])))
    return (float(np.median(cal)) if len(cal) > 5 else float("nan")), len(cal)


def run_scan(
    DV,
    G,
    Tw,
    uv,
    d0,
    mu0,
    W=WIDTH,
    d_half=14.0,
    n_d=19,
    mu_half=MU_HALF,
    vr=VR,
    workers=8,
    progress=None,
    cancel=None,
    inject=None,
    cc=None,
):
    """The scan around a candidate: distances d0 +- d_half au (n_d), radial speeds `vr`, own motions mu0 +- mu_half (step MU_STEP). -> dict(best (the box of best S/N), hyp (distance, radial speed, mu_u, mu_v per point),
    n_orbits, tk, sg, centre (S/N at the candidate's place, hypothesis), noise (blocks of the box away from it), nominal (flux, error in the grid units: divide by the scale))"""
    import multiprocessing as mp

    tk = np.array([v["t"] for v in DV])
    Rk = np.array([v["R"] for v in DV])
    w = np.array([1 / v["sg"] ** 2 for v in DV])
    A = np.array([v["d"] * wi for v, wi in zip(DV, w)], "f4")
    B = np.array([v["good"] * wi for v, wi in zip(DV, w)], "f4")
    a = np.arange(-mu_half, mu_half + 1e-9, MU_STEP)
    mus = np.array([(mu0[0] + x, mu0[1] + y) for x in a for y in a])
    Dg = np.linspace(d0 - d_half, d0 + d_half, n_d)
    Wp = dict(G=G, uv=uv, Tw=Tw, W=W, tk=tk, Rk=Rk, A=A, B=B, mus=mus, wsum=float(w.sum()))
    by, bx = BOX
    best = np.zeros((2 * by + 1, 2 * bx + 1), "f4")
    hyp = np.zeros(best.shape + (4,), "f4")
    jobs = [(float(d), float(v)) for d in Dg for v in vr]
    if workers and workers > 1:
        import pickle
        import tempfile
        import os

        fd, path = tempfile.mkstemp(suffix=".pkl")
        os.close(fd)
        with open(path, "wb") as fh:
            pickle.dump(Wp, fh, protocol=4)
        try:
            with mp.get_context("spawn").Pool(workers, initializer=_init_path, initargs=(path,)) as pool:
                for k, (d, vr_, b_, a_) in enumerate(pool.imap_unordered(scan, jobs)):
                    m = b_ > best
                    best[m] = b_[m]
                    hyp[m, 0], hyp[m, 1], hyp[m, 2], hyp[m, 3] = d, vr_, mus[a_[m], 0], mus[a_[m], 1]
                    if progress:
                        progress(k + 1, len(jobs))
                    if cancel is not None and cancel.is_set():
                        pool.terminate()
                        raise RuntimeError("cancelled")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    else:
        init(Wp)
        for k, jb in enumerate(jobs):
            d, vr_, b_, a_ = scan(jb)
            m = b_ > best
            best[m] = b_[m]
            hyp[m, 0], hyp[m, 1], hyp[m, 2], hyp[m, 3] = d, vr_, mus[a_[m], 0], mus[a_[m], 1]
            if progress:
                progress(k + 1, len(jobs))
    yy, xx = np.mgrid[-by : by + 1, -bx : bx + 1]
    far = np.hypot(xx, yy) > 5
    if inject is not None:
        far &= np.hypot(xx - inject[0], yy - inject[1]) > 5

    def at(ox, oy):
        sub = best[by + oy - 1 : by + oy + 2, bx + ox - 1 : bx + ox + 2]
        j = np.unravel_index(int(np.argmax(sub)), sub.shape)
        return float(sub.max()), [float(x) for x in hyp[by + oy - 1 + j[0], bx + ox - 1 + j[1]]]

    sc_, hc = at(0, 0)
    blocks = np.array(
        [best[i : i + 3, j : j + 3].max() for i in range(0, 2 * by - 1, 3) for j in range(0, 2 * bx - 1, 3) if far[i + 1, j + 1]]
    )
    # the nominal orbit alone (no scan): the flux the candidate would have, from the epochs where its place is not masked
    X0n, Vn = state(G, uv, d0, mu0, 0.0)
    pn = places(G, Tw, W, X0n, Vn, tk, Rk)[0]
    P0 = np.round(pn).astype(int)
    f0 = w0 = 0.0
    nk0 = 0
    for k in range(len(tk)):
        x, y = P0[k]
        if 0 <= y < A.shape[1] and 0 <= x < A.shape[2]:
            f0 += float(A[k, y, x])
            w0 += float(B[k, y, x])
            nk0 += int(B[k, y, x] > 0)
    out = dict(
        best=best,
        hyp=hyp,
        n_orbits=len(jobs) * len(mus),
        tk=tk,
        sg=[v["sg"] for v in DV],
        centre=(sc_, hc),
        blocks=blocks,
        wsum=float(w.sum()),
        nominal=(f0 / max(w0, 1e-12), 1 / np.sqrt(max(w0, 1e-12)), nk0, len(tk)),
        nominal_path=pn,
        masked=float(np.mean([v["masked"] for v in DV])),
    )
    if inject is not None:
        out["injected"] = at(*inject)
    return out


def _init_path(path):
    import pickle

    with open(path, "rb") as fh:
        init(pickle.load(fh))


def verdict(res, scale, flux_mjy):
    """what the WISE check says about a candidate of `flux_mjy` (mJy at 3.4 um; the SPHEREx flux of the candidate in D4 is in the same units to a colour term): the flux measured on the nominal orbit,
    the noise of one epoch and of all, the S/N of the scan at the place of the candidate against the noise blocks, and the S/N a body of the candidate's flux would give."""
    f0, e0, nk, nt = res["nominal"]
    meas, err = f0 / scale, e0 / scale
    blocks = res["blocks"]
    out = dict(
        flux_mjy=round(float(meas), 4),
        err_mjy=round(float(err), 4),
        epochs=int(nt),
        epochs_unmasked=int(nk),
        scan_sn=round(float(res["centre"][0]), 2),
        scan_hypothesis=[round(x, 3) for x in res["centre"][1]],
        noise_median=round(float(np.median(blocks)), 2) if len(blocks) else None,
        noise_p97=round(float(np.percentile(blocks, 97.5)), 2) if len(blocks) else None,
        noise_max=round(float(blocks.max()), 2) if len(blocks) else None,
        noise_places=int(len(blocks)),
        places_not_below_the_candidate=int((blocks >= res["centre"][0]).sum()),
    )
    if flux_mjy and err > 0:
        out["would_be_sigma"] = round(float(flux_mjy / err), 1)
        out["ruled_out"] = bool(meas < flux_mjy - 3 * err and flux_mjy / err > 5)
    return out
