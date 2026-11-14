"""M3 validation: the field engine on real exposures (docs/plan/02-methods/2.7-validation.md, V4–V12).

For each field: exposures and passes, the detectors of the white frame, cosmic-ray and per-visit rejections, the registration residual,
the target's motion (saturated-zone centre for a saturated star, the centroid of the brightest source otherwise), the FWHM of isolated
unsaturated stars in the white frame, the weight lattice of every stack, timings (cold and warm). Writes PNG renders of every pass (the
display law of the lab) next to the report for a person to look at.

Run:  uv run python -m validation.m3_field [--store store] [--fields barnard,epsindi] [--out-png DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

from engine.centroid import moffat_centre
from engine.colour import STAR_THRESHOLD, white_balance
from engine.halo import saturated_zones
from engine.register import STAR_FWHM_ARCSEC, stars
from indexer.store import Index
from service.reader.files import FileReader
from service.reader.s3 import S3
from service.workers.fieldbuild import FieldBuild, build_field
from validation.png import write_png

ROOT = Path(__file__).resolve().parent.parent
FIELDS = {
    "barnard": ("Barnard's Star", 269.4479, 4.7687, 7.0, True, (10.39, 356.0)),
    "epsindi": ("ε Indi B", 331.077, -56.794, 7.0, False, (4.58, 126.0)),
}


def stretch(x: np.ndarray, lo: float = -0.05, hi: float = 400.0, soft: float = 0.4) -> np.ndarray:
    return np.clip(np.arcsinh((x - lo) / soft) / np.arcsinh((hi - lo) / soft), 0, 1)


def render(b: FieldBuild, out: Path, tag: str) -> list[Path]:
    paths = []
    for p in b.passes:
        rgb = p.rgb(b.cdet)
        sat = p.sat_share(b.cdet) > 0.5
        r, g, bl = rgb
        star = np.isfinite(r) & (g > STAR_THRESHOLD) & ~sat
        kr, kb = white_balance(r, g, bl, star)
        rgb = np.stack([r * kr, g, bl * kb])
        img = np.stack([stretch(gaussian_filter(np.nan_to_num(c), 0.5)) for c in rgb], -1)
        img[~np.isfinite(rgb[0])] = (0.36, 0.40, 0.44)  # no data: flat blue-grey
        img[sat] = 1.0  # saturated: white
        path = out / f"{tag}_pass{p.index + 1}.png"
        write_png(path, img, scale=max(1, 600 // img.shape[0]))
        paths.append(path)
    return paths


def fwhm_of_stars(white: np.ndarray, sat: np.ndarray, step: float) -> tuple[float, int]:
    pos = stars(white, sat, step, nsig=25.0)
    img = np.nan_to_num(white)
    vals = []
    s = STAR_FWHM_ARCSEC / step / 2.3548
    for x, y in pos:
        xi, yi = round(x), round(y)
        r = int(4 * s)
        if not (r <= xi < img.shape[1] - r and r <= yi < img.shape[0] - r):
            continue
        cut = img[yi - r : yi + r + 1, xi - r : xi + r + 1]
        others = [q for q in pos if 0 < math.hypot(q[0] - x, q[1] - y) < 3 * r]
        if others:
            continue
        bg = np.median(np.concatenate([cut[0], cut[-1], cut[:, 0], cut[:, -1]]))
        c = cut - bg
        peak = c[r, r]
        if peak <= 0:
            continue
        area = (c > peak / 2).sum()  # pixels above half maximum: FWHM of an equivalent circular Gaussian
        vals.append(2 * math.sqrt(area / math.pi) * step)
    return (float(np.median(vals)) if vals else float("nan")), len(vals)


def lattice(b: FieldBuild) -> float:
    worst = 0.0
    for p in b.passes:
        for k, d in p.den.items():
            if not k.startswith("D"):
                continue
            n = d.shape[0]
            core = d[n // 4 : 3 * n // 4, n // 4 : 3 * n // 4].astype(float)
            if core.mean() <= 0:
                continue
            ratio = core / np.maximum(gaussian_filter(core, 6.0), 1e-12)
            worst = max(worst, float(ratio.std()))
    return worst


def target_track(b: FieldBuild, saturated: bool) -> list[tuple[float, float, float]]:
    """(mjd_mid, ra, dec) of the target per pass: a Moffat fit to the usable pixels of every exposure (the wings of a saturated core),
    in registered grid coordinates, median over the pass. First guess: the saturated zone (or brightest source) near the centre."""
    out = []
    c = b.grid.centre_px
    step = b.grid.step_arcsec
    for p in b.passes:
        if saturated:
            z = saturated_zones(p.sat_share(b.cdet), step)
            if not z:
                continue
            gx, gy, _ = min(z, key=lambda t: math.hypot(t[0] - c, t[1] - c))
        else:
            w = np.nan_to_num(p.white(b.cdet))
            r = int(30 / step)
            sub = gaussian_filter(w, 2)[int(c) - r : int(c) + r, int(c) - r : int(c) + r]
            j, i = np.unravel_index(np.argmax(sub), sub.shape)
            gx, gy = float(i + int(c) - r), float(j + int(c) - r)
        reg = p.registration
        dx, dy = (reg.dx, reg.dy) if reg else (0.0, 0.0)
        fits = []
        for e in b.natives.get(p.index, []):
            got = moffat_centre(e.u, e.v, e.val, gx - dx, gy - dy, step)
            if got is not None:
                fits.append((got[0] + dx, got[1] + dy))
        if not fits:
            continue
        x, y = np.median(np.asarray(fits), axis=0)
        ra, dec = b.grid.grid_to_sky([x], [y])
        out.append((0.5 * (p.mjd0 + p.mjd1), float(ra[0]), float(dec[0])))
    return out


def motion(track: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """(rate ″/yr, PA °, shift first→last ″) of a straight-line fit."""
    t = np.array([(m - track[0][0]) / 365.25 for m, _, _ in track])
    dec0 = track[0][2]
    x = np.array([(ra - track[0][1]) * math.cos(math.radians(dec0)) * 3600 for _, ra, _ in track])
    y = np.array([(dec - track[0][2]) * 3600 for _, _, dec in track])
    vx, vy = np.polyfit(t, x, 1)[0], np.polyfit(t, y, 1)[0]
    shift = math.hypot(x[-1] - x[0], y[-1] - y[0])
    return math.hypot(vx, vy), math.degrees(math.atan2(vx, vy)) % 360, shift


async def run_field(name: str, index: Index, store: Path, png: Path, workers: int) -> list[str]:
    label, ra, dec, fov, saturated, (cat_rate, cat_pa) = FIELDS[name]
    lines = [f"### {label} — RA {ra}, Dec {dec}, {fov:.0f}′", ""]
    s3 = S3(max_in_flight=48)
    fr = FileReader(s3, store)
    with ProcessPoolExecutor(workers) as ex:
        t = time.time()
        b = await build_field(index, fr, ra, dec, fov, executor=ex, keep_natives=True)
        cold = time.time() - t
        t = time.time()
        await build_field(index, FileReader(s3, store), ra, dec, fov, executor=ex)
        warm = time.time() - t
    await s3.close()
    lines.append(f"- exposures: {len(b.exposures)} of {b.thinned_from} overlapping; not readable: {len(b.missing)}")
    lines.append(f"- passes: {len(b.passes)}; white frame from D{', D'.join(map(str, b.cdet))}")
    for p in b.passes:
        reg = p.registration
        st = b.grid.step_arcsec
        regs = (
            f"shift ({reg.dx * st:+.2f}″, {reg.dy * st:+.2f}″), {reg.n_stars} stars, scatter {reg.scatter_arcsec:.2f}″"
            if reg
            else "reference"
        )
        lines.append(
            f"  - pass {p.index + 1}: MJD {p.mjd0:.1f}–{p.mjd1:.1f}, exposures {sum(p.detectors.values())} "
            f"{dict(sorted(p.detectors.items()))}; cosmic-ray pixels {sum(p.rejected.values())} {dict(sorted(p.rejected.items()))}; "
            f"per-visit pixels {p.visit_rejected}; registration: {regs}"
        )
    tr = target_track(b, saturated)
    if len(tr) >= 2:
        rate, pa, shift = motion(tr)
        years = (tr[-1][0] - tr[0][0]) / 365.25
        lines.append(
            f"- target motion: **{rate:.2f} ″/yr toward {pa:.0f}°** (catalogue {cat_rate} ″/yr, {cat_pa:.0f}°); "
            f"shift first→last {shift:.2f}″ over {years:.2f} yr"
        )
    p0 = b.passes[0]
    fw, nst = fwhm_of_stars(p0.white(b.cdet), p0.sat_share(b.cdet), b.grid.step_arcsec)
    lines.append(f"- FWHM of isolated unsaturated stars in the white frame of pass 1: {fw:.1f}″ ({nst} stars)")
    lines.append(f"- weight lattice (largest relative rms of a detector stack's weight in the central half): {100 * lattice(b):.1f} %")
    lines.append(
        f"- time: cold {cold:.0f} s (reading {b.timings['read_s']:.0f} s, computing {b.timings['compute_s']:.0f} s), warm {warm:.0f} s"
    )
    paths = render(b, png, name)
    lines.append(f"- renders: {', '.join(p.name for p in paths)}")
    lines.append("")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, default=ROOT / "store")
    ap.add_argument("--fields", default="barnard,epsindi")
    ap.add_argument("--out-png", type=Path, default=ROOT / "store" / "renders")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    a.out_png.mkdir(parents=True, exist_ok=True)
    index = Index(a.store / "index" / "exposures.sqlite", readonly=True)
    rep = [f"# M3 validation — the field engine on real exposures ({datetime.now(UTC).date().isoformat()})", ""]
    for name in a.fields.split(","):
        rep += asyncio.run(run_field(name, index, a.store, a.out_png, a.workers))
    out = ROOT / "docs" / "validation" / f"{datetime.now(UTC).date().isoformat()}-m3-field.md"
    out.write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
