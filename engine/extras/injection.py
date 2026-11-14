"""Synthetic movers injected into film motion maps, and the recall of the tracked-stack search against their brightness and rate.

A mover is a point source of the film's resolution (a Gaussian of the film's FWHM) added to the noise-normalised motion maps of the
frames along a straight path; masked pixels stay masked, so a body that crosses a masked place disappears there, as a real one would.
The amplitude is the per-frame peak in noise units; `amplitude_for_mag` turns a magnitude into one with a reference body measured in
the same film (the default is a calibration of a V 18.9 asteroid at about 4.5 sigma per visit and must be replaced by the film's own).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from engine.extras import tracking

FWHM_SIGMA = 2.3548


def amplitude_for_mag(mag: float, ref_mag: float = 18.9, ref_amp: float = 4.5) -> float:
    """Per-frame peak (noise units) of a body of magnitude `mag`, scaled from a reference body of `ref_mag` that shows `ref_amp`."""
    return float(ref_amp * 10.0 ** (-0.4 * (mag - ref_mag)))


@dataclass(frozen=True)
class Mover:
    x0: float  # column and row at t_ref (hours)
    y0: float
    v_col: float  # pixels per hour
    v_row: float
    amplitude: float
    t_ref_h: float = 0.0


def inject(z: NDArray[np.floating], t_h: NDArray[np.floating], m: Mover, fwhm_px: float) -> NDArray[np.float32]:
    """A copy of `z` (frames, rows, columns) with the mover added where the pixel is finite."""
    out = np.array(z, dtype=np.float32, copy=True)
    s = fwhm_px / FWHM_SIGMA
    r = math.ceil(4 * s) + 1
    f, h, w = out.shape
    for k in range(f):
        cx = m.x0 + m.v_col * (t_h[k] - m.t_ref_h)
        cy = m.y0 + m.v_row * (t_h[k] - m.t_ref_h)
        x0, x1 = max(0, int(cx) - r), min(w, int(cx) + r + 2)
        y0, y1 = max(0, int(cy) - r), min(h, int(cy) + r + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        stamp = m.amplitude * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * s * s))
        block = out[k, y0:y1, x0:x1]
        out[k, y0:y1, x0:x1] = np.where(np.isfinite(block), block + stamp, block)
    return out


@dataclass
class Recall:
    amplitudes: list[float]
    rates_arcsec_h: list[float]
    recall: list[list[float]]  # [amplitude][rate]
    mean_snr: list[list[float]]
    threshold: float
    noise_peaks: list[float] = field(default_factory=list)
    trials: int = 0


def _random_mover(rng: np.random.Generator, shape: tuple[int, int], t_h: NDArray[np.floating], rate_px_h: float, amp: float) -> Mover:
    h, w = shape
    tr = float(np.mean(t_h))
    for _ in range(100):
        ang = rng.uniform(0, 2 * math.pi)
        vc, vr = rate_px_h * math.cos(ang), rate_px_h * math.sin(ang)
        x0, y0 = rng.uniform(0.15 * w, 0.85 * w), rng.uniform(0.15 * h, 0.85 * h)
        ends = [(x0 + vc * (t - tr), y0 + vr * (t - tr)) for t in (float(np.min(t_h)), float(np.max(t_h)))]
        if all(6 <= x < w - 6 and 6 <= y < h - 6 for x, y in ends):
            return Mover(x0, y0, vc, vr, amp, tr)
    return Mover(0.5 * w, 0.5 * h, 0.0, 0.0, amp, tr)


def recall_grid(
    z: NDArray[np.floating],
    t_h: NDArray[np.floating],
    step_arcsec: float,
    amplitudes: list[float],
    rates_arcsec_h: list[float],
    trials: int = 10,
    seed: int = 1,
    fwhm_arcsec: float = 3.2,
    false_alarm: float = 0.05,
    smear_px: float = 1.0,
) -> Recall:
    """Recall of the tracked-stack search for injected movers of each amplitude and rate.

    The detection threshold is the (1 - false_alarm) quantile of the strongest peak in the film without any mover (`trials` blind
    searches: the film itself and copies with the frames in random order); a mover counts as found when the strongest peak is above it,
    within 3 pixels of the true place and within two velocity cells of the true velocity.
    """
    rng = np.random.default_rng(seed)
    vmax = max(rates_arcsec_h) * 1.25
    span = float(np.max(t_h) - np.min(t_h))
    dv = smear_px / span if span > 0 else 1.0
    first = tracking.search(z, t_h, step_arcsec, vmax, smear_px, top=1)
    noise = [first[0].snr] if first else []
    # the noise-only distribution needs more than one realisation: draw the film's own frames in a random order (breaks any real motion)
    for _ in range(max(0, trials - 1)):
        order = rng.permutation(z.shape[0])
        shuffled = tracking.search(z[order], t_h, step_arcsec, vmax, smear_px, top=1)
        if shuffled:
            noise.append(shuffled[0].snr)
    thr = float(np.quantile(noise, 1.0 - false_alarm)) if noise else float("inf")
    fwhm_px = fwhm_arcsec / step_arcsec
    rec, snrs = [], []
    for amp in amplitudes:
        row_r, row_s = [], []
        for rate in rates_arcsec_h:
            hits, vals = 0, []
            for _ in range(trials):
                m = _random_mover(rng, z.shape[1:], t_h, rate / step_arcsec, amp)
                peaks = tracking.search(inject(z, t_h, m, fwhm_px), t_h, step_arcsec, vmax, smear_px, top=1)
                if not peaks:
                    vals.append(0.0)
                    continue
                p = peaks[0]
                vals.append(p.snr)
                near = abs(p.x - m.x0) <= 3 and abs(p.y - m.y0) <= 3
                same_v = abs(p.v_col - m.v_col) <= 2 * dv and abs(p.v_row - m.v_row) <= 2 * dv
                if p.snr >= thr and near and same_v:
                    hits += 1
            row_r.append(hits / trials)
            row_s.append(float(np.mean(vals)))
        rec.append(row_r)
        snrs.append(row_s)
    return Recall(list(amplitudes), list(rates_arcsec_h), rec, snrs, thr, noise, trials)
