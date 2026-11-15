"""Red sources of a field: what is bright at long wavelengths and faint at short ones (cold brown dwarfs, dusty and very red objects).

The detector stacks of every pass are co-added by inverse variance and sources are found on the long-wavelength image (D5–D6,
3.8–5 µm). Each source is then measured pass by pass at that pass's own long-wavelength peak (a cold brown dwarf moves several
arcseconds a year, and a star beside it would otherwise blend into it), in an aperture of 0.6 detector pixel, in D2 (1.1–1.6 µm) and
in the long image. The colour (the ratio of the two surface brightnesses, MJy/sr, so of the fluxes) is that of the reddest pass in
which the source stands at S/N ≥ 5 in the long image on its own: a moving dwarf may sit on top of a background star in some passes
and beside it in others. Sources with a saturated core are left out. A source is red when its colour stands far above the field's
own stars (the median + 4 robust σ in log colour, between a factor 2.5 and 4), or when it is seen in the long image and not in D2.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter, maximum_filter

from engine.grid import NATIVE_PIXEL_ARCSEC

LONG = (4, 5)  # D5, D6: 3.8–5 µm, where a cold brown dwarf is brightest (methane darkens it near 3.3 µm, in D4)
SHORT = 1  # D2
NSIG_DETECT = 6.0  # a source needs this S/N in the aperture sum over the passes
NSIG_PEAK = 4.0  # a peak of the smoothed long-wavelength image is looked at from this height above the field's scatter (the scatter grows
# with the crowding of the field, so a fixed height there would lose faint sources in big fields; the aperture S/N above is the real cut)
EDGE_PX = 8


@dataclass
class Red:
    x: float
    y: float
    snr_long: float
    snr_short: float
    colour: float  # long / short flux ratio (a lower limit when the source is not seen in D2)
    lower_limit: bool
    red: bool
    moves: bool = False  # the long-wavelength peak shifts by more than half a detector pixel between the first and last pass
    path: list[tuple[int, float, float]] | None = None  # (pass, x, y) of the long-wavelength peak in every pass


def _coadd(stacks: list[NDArray[np.floating]], noises: list[NDArray[np.floating]]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    num = np.zeros(stacks[0].shape, np.float64)
    den = np.zeros(stacks[0].shape, np.float64)
    for v, s in zip(stacks, noises, strict=True):
        ok = np.isfinite(v) & np.isfinite(s) & (s > 0)
        w = np.where(ok, 1.0 / np.where(ok, s, 1.0) ** 2, 0.0)
        num += np.where(ok, v, 0.0) * w
        den += w
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan), np.where(den > 0, 1.0 / np.sqrt(den), np.nan)


def _aperture(img: NDArray[np.float64], sig: NDArray[np.float64], x: float, y: float, r: float) -> tuple[float, float]:
    """Flux and error in the aperture of radius r at (x, y), the background from the ring 2.5 r to 4 r (only that window is read)."""
    n = img.shape[0]
    w = int(np.ceil(4 * r)) + 1
    x0, x1 = max(0, int(np.floor(x)) - w), min(n, int(np.floor(x)) + w + 2)
    y0, y1 = max(0, int(np.floor(y)) - w), min(n, int(np.floor(y)) + w + 2)
    if x1 <= x0 or y1 <= y0:
        return float("nan"), float("nan")
    cut, cut_sig = img[y0:y1, x0:x1], sig[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.hypot(xx - x, yy - y)
    inner = (d <= r) & np.isfinite(cut)
    ring = (d > 2.5 * r) & (d < 4 * r) & np.isfinite(cut)
    if inner.sum() < 3 or ring.sum() < 10:
        return float("nan"), float("nan")
    bg = float(np.median(cut[ring]))
    f = float(np.sum(cut[inner] - bg))
    e = float(np.sqrt(np.nansum(cut_sig[inner] ** 2)) + 1.4826 * np.median(np.abs(cut[ring] - bg)) * np.sqrt(inner.sum()) * 0.5)
    return f, e


def _offset_from(img: NDArray[np.float64], x: float, y: float, r: float) -> float:
    """How far (detector pixels' worth of the image's own pixels) the light centroid within 1.5 r of (x, y) lies from (x, y): near zero for
    a source that stands there, large when the light is a neighbour's wing (the ring around, 2.5 r to 4 r, gives the background)."""
    n = img.shape[0]
    w = int(np.ceil(4 * r)) + 1
    x0, x1 = max(0, int(np.floor(x)) - w), min(n, int(np.floor(x)) + w + 2)
    y0, y1 = max(0, int(np.floor(y)) - w), min(n, int(np.floor(y)) + w + 2)
    cut = img[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.hypot(xx - x, yy - y)
    ring = (d > 2.5 * r) & (d < 4 * r) & np.isfinite(cut)
    inner = (d <= 1.5 * r) & np.isfinite(cut)
    if ring.sum() < 10 or inner.sum() < 3:
        return 0.0
    wgt = np.where(inner, np.maximum(cut - float(np.median(cut[ring])), 0.0), 0.0)
    tot = float(wgt.sum())
    if tot <= 0:
        return 0.0
    return float(np.hypot(float((wgt * xx).sum()) / tot - x, float((wgt * yy).sum()) / tot - y))


def red_sources(
    dets: list[NDArray[np.floating]],
    noise: list[NDArray[np.floating]],
    step_arcsec: float,
    sats: list[NDArray[np.floating]] | None = None,
) -> list[Red]:
    """`dets[k]`, `noise[k]`: the six detector stacks of pass k and their model noise (6 × n × n)."""
    per_det = [_coadd([d[i] for d in dets], [s[i] for s in noise]) for i in range(6)]
    long_v, _ = _coadd([per_det[i][0] for i in LONG], [per_det[i][1] for i in LONG])
    short_v, _ = per_det[SHORT]
    native = NATIVE_PIXEL_ARCSEC / step_arcsec
    sm = gaussian_filter(np.nan_to_num(long_v), 0.8 * native)
    bg = gaussian_filter(np.nan_to_num(long_v), 6 * native)
    resid = sm - bg
    mad = 1.4826 * float(np.nanmedian(np.abs(resid - np.nanmedian(resid)))) or 1e-9
    n = long_v.shape[0]
    box = max(3, int(2 * native) | 1)
    peaks = (resid == maximum_filter(resid, size=box)) & (resid > NSIG_PEAK * mad) & np.isfinite(long_v) & np.isfinite(short_v)
    peaks[:EDGE_PX] = peaks[-EDGE_PX:] = False
    peaks[:, :EDGE_PX] = peaks[:, -EDGE_PX:] = False
    passes_long = [_coadd([d[i] for i in LONG], [sg[i] for i in LONG]) for d, sg in zip(dets, noise, strict=True)]
    passes_short = [(d[SHORT].astype(np.float64), sg[SHORT].astype(np.float64)) for d, sg in zip(dets, noise, strict=True)]
    smooth_long = [gaussian_filter(np.nan_to_num(v), 0.8 * native) for v, _ in passes_long]
    r_ap = 0.6 * native
    search = max(2, round(1.0 * native))
    out: list[Red] = []
    for y, x in zip(*np.nonzero(peaks), strict=True):
        if sats is not None and any(
            float(np.nanmax(sm_[max(0, y - search) : y + search + 1, max(0, x - search) : x + search + 1])) > 0.3 for sm_ in sats
        ):
            continue  # a saturated core: its short-wavelength light is lost, its colour would be false
        rows: list[list[float]] = []  # per pass: a_l, e_l, a_s, e_s
        where: list[tuple[float, float]] = []
        path: list[tuple[int, float, float]] = []
        for kk, ((lv, ls), (sv, ss), sm_k) in enumerate(zip(passes_long, passes_short, smooth_long, strict=True)):
            y0, y1 = max(0, y - search), min(n, y + search + 1)
            x0, x1 = max(0, x - search), min(n, x + search + 1)
            win = sm_k[y0:y1, x0:x1]
            if not np.isfinite(win).any():
                continue
            yy, xx = np.unravel_index(int(np.nanargmax(win)), win.shape)
            px, py = float(x0 + xx), float(y0 + yy)
            a_l, e_l = _aperture(lv, ls, px, py, r_ap)
            a_s, e_s = _aperture(sv, ss, px, py, r_ap)
            if not (np.isfinite(a_l) and np.isfinite(a_s)) or e_l <= 0 or e_s <= 0:
                continue
            if a_s / e_s >= 3.0 and _offset_from(sv, px, py, r_ap) > 0.25 * native:
                a_s = 0.0  # short-wavelength light not centred on the source is a neighbour's (a star beside a cold dwarf), not its own
            rows.append([a_l, e_l, a_s, e_s])
            where.append((px, py))
            path.append((kk, px, py))
        # a pass in which the short light is far above what another pass of the same source allows (its 3 sigma limit, twice) is a blend
        # with a star standing on the source in that pass
        limits = [3.0 * q[3] / q[0] for q in rows if q[0] > 0 and q[2] / q[3] < 3.0]
        if limits:
            cap = 2.0 * min(limits)
            for q in rows:
                if q[0] > 0 and q[2] > 0 and q[2] / q[0] > cap:
                    q[2] = 0.0
        fl = sum(q[0] for q in rows)
        fs = sum(q[2] for q in rows)
        vl = sum(q[1] ** 2 for q in rows)
        vs = sum(q[3] ** 2 for q in rows)
        best: tuple[float, bool] | None = None
        for a_l, e_l, a_s, e_s in rows:
            if a_l / e_l >= 5.0:
                lim = a_s / e_s < 3.0
                c = a_l / (3.0 * e_s if lim else a_s)
                if best is None or c > best[0]:
                    best = (c, lim)
        if vl <= 0 or vs <= 0 or fl <= 0 or best is None:
            continue
        el, es = float(np.sqrt(vl)), float(np.sqrt(vs))
        snr_l, snr_s = fl / el, fs / es
        if snr_l < NSIG_DETECT:
            continue
        moves = len(where) >= 2 and float(np.hypot(where[-1][0] - where[0][0], where[-1][1] - where[0][1])) > 0.5 * native
        out.append(Red(float(x), float(y), float(snr_l), float(snr_s), float(best[0]), best[1], False, moves, path))
    seen = [r for r in out if not r.lower_limit and r.snr_short >= 5]
    if len(seen) >= 5:
        lc = np.log10([r.colour for r in seen])
        med = float(np.median(lc))
        sc = 1.4826 * float(np.median(np.abs(lc - med)))
        thr = med + min(max(np.log10(2.5), 4 * sc), np.log10(4.0))
        for r in out:
            r.red = np.log10(r.colour) > thr or (r.lower_limit and r.snr_long >= 8 and np.log10(r.colour) > med + np.log10(3.0))
    return sorted(out, key=lambda r: -r.colour)
