"""The field job: builds a field and publishes its parts pass by pass (docs/plan/05-architecture.md 5.5, 5.7).

Parts (SXP1, row 0 = north, column 0 = east):
    quick_p{k}.sxp       the quick white frame of pass k (2–4 exposures per detector, published first) and its saturated share
    meta.json            grid, passes, detectors of the white frame, bands, counts, timings, white balance per pass
    picture_p{k}.sxp     R, G, B (float16, NaN = no data) and the saturated share (uint8 × 255) of pass k
    dets_p{k}.sxp        the six detector stacks (float16, NaN = no data) and their model noise
    bands_p{k}.sxp       the named-band stacks (float16)
    flags_p{k}.sxp       the flag census (uint8 share × 255 per flag name)
    diff.sxp             z of every pass against the first, white frame and each band (uint8: 128 + 12.7 z, clipped at ±10; 0 = no data)
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from engine.bands import load_bands
from engine.colour import STAR_THRESHOLD, white_balance
from engine.diff import z_map
from engine.field import PassProducts
from engine.grid import grid_for_fov
from indexer.store import Index
from service.jobs.scheduler import Context
from service.products.store import Store
from service.products.sxp import encode
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.workers.fieldbuild import build_field

FIELD_SIZES = (2.0, 5.0, 10.0, 20.0, 60.0)
KIND = "field"


def canonical(ra: float, dec: float, fov: float) -> dict[str, Any]:
    """The address rounding: RA to 0.01 s of time, Dec to 0.1″, the field size to one of the five sizes."""
    ra_s = round((ra % 360.0) / 15.0 * 3600.0, 2)
    dec_as = round(dec * 3600.0, 1)
    size = min(FIELD_SIZES, key=lambda f: abs(f - fov))
    return {"ra": ra_s * 15.0 / 3600.0, "dec": dec_as / 3600.0, "fov": size}


def memory_estimate_mb(fov: float) -> int:
    n = grid_for_fov(0.0, 0.0, fov).n
    return int(400 + 6 * n * n * 4 * 22 / 1e6)  # a detector group plus the band sums and the pass layers


def _f16(a: np.ndarray) -> np.ndarray:
    return np.asarray(a, np.float32).astype(np.float16)


def _encode_z(z: np.ndarray) -> np.ndarray:
    out = np.zeros(z.shape, np.uint8)
    ok = np.isfinite(z)
    out[ok] = np.clip(np.round(128 + 12.7 * np.clip(z[ok], -10, 10)), 1, 255).astype(np.uint8)
    return out


def _white_noise(layers: list[np.ndarray]) -> np.ndarray:
    """Model noise of the white frame (the mean of the detector stacks): sqrt(Σσ²)/m where every detector has a value."""
    st = np.stack(layers).astype(np.float64)
    ok = np.isfinite(st).all(axis=0)
    out = np.sqrt(np.square(np.nan_to_num(st)).sum(axis=0)) / max(1, len(layers))
    return np.where(ok, out, np.nan).astype(np.float32)


def pass_meta(pp: PassProducts, kr: float, kb: float) -> dict[str, Any]:
    reg = pp.registration
    return {
        "index": pp.index,
        "mjd0": pp.mjd0,
        "mjd1": pp.mjd1,
        "mjd_mid": 0.5 * (pp.mjd0 + pp.mjd1),
        "detectors": {f"D{d}": n for d, n in sorted(pp.detectors.items())},
        "rejected": {f"D{d}": n for d, n in sorted(pp.rejected.items())},
        "visit_rejected": pp.visit_rejected,
        "registration": None if reg is None else {"dx": reg.dx, "dy": reg.dy, "stars": reg.n_stars, "scatter_arcsec": reg.scatter_arcsec},
        "white_balance": {"kr": kr, "kb": kb},
    }


async def run_field(
    ctx: Context, index: Index, fr: FileReader, store: Store, params: dict[str, Any], wavelengths: WavelengthMaps | None = None
) -> dict[str, Any]:
    key = ctx.job.key
    store.begin(KIND, key, params)
    bands = load_bands()
    passes_meta: list[dict[str, Any]] = []
    built: list[PassProducts] = []

    async def on_pass(pp: PassProducts, cdet: tuple[int, ...]) -> None:
        k = pp.index
        rgb = pp.rgb(cdet)
        sat = pp.sat_share(cdet)
        r, g, b = rgb
        kr, kb = white_balance(r, g, b, np.isfinite(r) & (g > STAR_THRESHOLD) & (sat < 0.5))
        store.write_part(
            KIND, key, f"picture_p{k}.sxp", encode({"rgb": _f16(rgb), "sat": (np.clip(sat, 0, 1) * 255).astype(np.uint8)}, {"pass": k})
        )
        dets = np.stack([pp.layer(f"D{d}") for d in range(1, 7)])
        noise = np.stack([pp.noise(f"D{d}") for d in range(1, 7)])
        store.write_part(
            KIND,
            key,
            f"dets_p{k}.sxp",
            encode({"dets": _f16(dets), "noise": _f16(noise)}, {"pass": k, "layers": [f"D{d}" for d in range(1, 7)]}),
        )
        bl = np.stack([pp.layer(b.key) for b in bands])
        store.write_part(KIND, key, f"bands_p{k}.sxp", encode({"bands": _f16(bl)}, {"pass": k, "layers": [b.key for b in bands]}))
        names = sorted(pp.census)
        if names:
            fl = np.stack([(pp.census[n] * 255).astype(np.uint8) for n in names])
            store.write_part(KIND, key, f"flags_p{k}.sxp", encode({"flags": fl}, {"pass": k, "names": names}))
        passes_meta.append(pass_meta(pp, kr, kb))
        built.append(pp)
        for part in ("picture", "dets", "bands", "flags"):
            ctx.part_ready(f"{part}_p{k}")
        ctx.phase(f"pass {k + 1}: published", len(passes_meta), None)

    async def on_quick(pp: PassProducts, cdet: tuple[int, ...]) -> None:
        k = pp.index
        white = pp.white(cdet)
        sat = pp.sat_share(cdet)
        store.write_part(
            KIND, key, f"quick_p{k}.sxp", encode({"white": _f16(white), "sat": (np.clip(sat, 0, 1) * 255).astype(np.uint8)}, {"pass": k})
        )
        ctx.part_ready(f"quick_p{k}")

    def progress(text: str) -> None:
        ctx.phase(text)

    b = await build_field(
        index,
        fr,
        params["ra"],
        params["dec"],
        params["fov"],
        executor=ctx._s.executor,
        progress=progress,
        checkpoint=ctx.checkpoint,
        on_pass=on_pass,
        on_quick=on_quick,
        wavelengths=wavelengths,
    )
    ctx.phase("comparing passes")
    if len(built) >= 2:
        z = {}
        for pp in built[1:]:
            names = ["white", *[bd.key for bd in bands]]
            for name in names:
                if name == "white":
                    a, c = built[0].white(b.cdet), pp.white(b.cdet)
                    sa = _white_noise([built[0].noise(f"D{d}") for d in b.cdet])
                    sc = _white_noise([pp.noise(f"D{d}") for d in b.cdet])

                else:
                    a, c, sa, sc = built[0].layer(name), pp.layer(name), built[0].noise(name), pp.noise(name)
                z[f"p{pp.index}_{name}"] = _encode_z(z_map(a, c, sa, sc, b.grid.step_arcsec))
        store.write_part(KIND, key, "diff.sxp", encode(z, {"reference_pass": 0, "encoding": "128 + 12.7 z, clipped at ±10; 0 = no data"}))
        ctx.part_ready("diff")
    meta = {
        "kind": KIND,
        "params": params,
        "grid": {
            "ra0": b.grid.ra0,
            "dec0": b.grid.dec0,
            "step_arcsec": b.grid.step_arcsec,
            "n": b.grid.n,
            "orientation": "row 0 north, column 0 east (east left)",
        },
        "cdet": list(b.cdet),
        "bands": [{"key": bd.key, "name": bd.name, "label": bd.label, "hue": bd.hue} for bd in bands],
        "passes": sorted(passes_meta, key=lambda m: m["index"]),
        "exposures": len(b.exposures),
        "overlapping": b.thinned_from,
        "not_readable": len(b.missing),
        "timings": {k: round(v, 2) for k, v in b.timings.items()},
    }
    store.write_meta(KIND, key, meta)
    store.complete(key)
    ctx.part_ready("meta")
    return {"exposures": len(b.exposures), "passes": len(built)}


def exposure_signature(index: Index, params: dict[str, Any]) -> str:
    """What the field is built from: its overlapping exposures (a new period changes only the keys of the fields it touches)."""
    found = index.overlapping(params["ra"], params["dec"], params["fov"] / 60.0 / 2.0 * 2**0.5)
    import hashlib

    return hashlib.sha256(json.dumps(sorted(e.key for e in found)).encode()).hexdigest()[:16]
