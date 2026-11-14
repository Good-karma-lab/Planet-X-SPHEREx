"""Recall of the search between passes, measured by injecting moving point sources into the real white stacks of fields.

For each field (meta.json and quick_p*.sxp downloaded from the service into a folder), a few point sources at a time are added to every
pass's white stack — a Gaussian of the measured star width (FWHM 8.8″), its peak a chosen multiple of the stack's robust noise, moving
in a straight line at a chosen speed and a random direction from a random place — and the search (engine/movers.py) is run. A source is
recovered when a track passes within 3″ of its places in at least three passes with a speed within 25 %.
    uv run python validation/v_injection.py <folder> [field ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.movers import search
from service.products.sxp import decode

SNRS = [5, 8, 12, 20, 40]
SPEEDS = [5, 10, 30, 100]  # ″/yr
PER_RUN = 4
RUNS = 5
FWHM = 8.8
WITH_WEAK = "--weak" in sys.argv


def load(folder: Path, name: str) -> tuple[list[np.ndarray], list[np.ndarray], list[float], float]:
    meta = json.loads((folder / f"{name}.meta.json").read_text())
    whites, sats = [], []
    for p in meta["passes"]:
        _, a = decode((folder / f"{name}_quick_p{p['index']}.sxp").read_bytes())
        whites.append(a["white"].astype(np.float32))
        sats.append(a["sat"].astype(np.float32) / 255.0)
    return whites, sats, [p["mjd_mid"] for p in meta["passes"]], meta["grid"]["step_arcsec"]


def main() -> int:
    folder = Path(sys.argv[1])
    names = [a for a in sys.argv[2:] if not a.startswith("--")]
    names = names or sorted(p.name.removesuffix(".meta.json") for p in folder.glob("*.meta.json"))
    rng = np.random.default_rng(7)
    table: dict[tuple[int, int], list[int]] = {}
    for name in names:
        whites, sats, mjds, step = load(folder, name)
        n = whites[0].shape[0]
        years = (np.asarray(mjds) - mjds[0]) / 365.25
        ok = np.logical_and.reduce([np.isfinite(w) for w in whites])
        noise = float(np.median([1.4826 * np.nanmedian(np.abs(w[ok] - np.nanmedian(w[ok]))) for w in whites]))
        sig = FWHM / 2.3548 / step
        yy, xx = np.mgrid[0:n, 0:n]
        base = search(whites, sats, mjds, step, trials=0)
        base_tracks = len(base.tracks)
        for snr in SNRS:
            for v in SPEEDS:
                hit = tot = 0
                for _ in range(RUNS):
                    injected = []
                    imgs = [w.copy() for w in whites]
                    for _ in range(PER_RUN):
                        pa = rng.uniform(0, 2 * np.pi)
                        vx, vy = -v * np.sin(pa) / step, -v * np.cos(pa) / step  # east is −x, north is −y
                        span = abs(years[-1]) * np.hypot(vx, vy)
                        x0 = rng.uniform(30 + span, n - 30 - span)
                        y0 = rng.uniform(30 + span, n - 30 - span)
                        places = [(x0 + vx * t, y0 + vy * t) for t in years]
                        for img, (px, py) in zip(imgs, places, strict=True):
                            img += (snr * noise * np.exp(-((xx - px) ** 2 + (yy - py) ** 2) / (2 * sig * sig))).astype(np.float32)
                        injected.append((places, v))
                    res = search(imgs, sats, mjds, step, trials=0)
                    for places, vv in injected:
                        tot += 1
                        for t in res.tracks + (res.weak_tracks if WITH_WEAK else []):
                            d = [np.hypot(t.xy[i][0] - places[k][0], t.xy[i][1] - places[k][1]) * step for i, k in enumerate(t.passes)]
                            if len(t.passes) >= 3 and max(d) < 3.0 and abs(t.speed - vv) < 0.25 * vv:
                                hit += 1
                                break
                table.setdefault((snr, v), []).extend([hit, tot])
        print(f"{name}: {len(whites)} passes, noise {noise:.3g}, real tracks {base_tracks}", file=sys.stderr)
    print("| peak S/N | " + " | ".join(f"{v}″/yr" for v in SPEEDS) + " |")
    print("|---|" + "---|" * len(SPEEDS))
    for snr in SNRS:
        cells = []
        for v in SPEEDS:
            vals = table[(snr, v)]
            h, t = sum(vals[0::2]), sum(vals[1::2])
            cells.append(f"{100 * h / t:.0f} % ({h}/{t})")
        print(f"| {snr} | " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
