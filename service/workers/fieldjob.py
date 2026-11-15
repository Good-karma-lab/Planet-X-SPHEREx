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
import math
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


def _rounded(ra: float, dec: float, fov: float) -> dict[str, Any]:
    ra_s = round((ra % 360.0) / 15.0 * 3600.0, 2)
    dec_as = round(dec * 3600.0, 1)
    size = min(FIELD_SIZES, key=lambda f: abs(f - fov))
    return {"ra": ra_s * 15.0 / 3600.0, "dec": dec_as / 3600.0, "fov": size}


# the centres of the fields the service already has (the warm set and every field in the store), by size: a request this close to one
# is that field (SNAP of the field size), so a link a few arcseconds off never rebuilds a field that is ready
SNAP = 1.0 / 20.0
_CENTRES: dict[float, set[tuple[float, float]]] = {}
_WARM: dict[float, set[tuple[float, float]]] = {}  # the warm set's centres win over any other field nearby


def remember(ra: float, dec: float, fov: float, warm: bool = False) -> None:
    """A field the service has (or warms): later requests near it snap to its centre; a warm field is preferred."""
    p = _rounded(ra, dec, fov)
    (_WARM if warm else _CENTRES).setdefault(p["fov"], set()).add((p["ra"], p["dec"]))


def canonical(ra: float, dec: float, fov: float) -> dict[str, Any]:
    """The address rounding: RA to 0.01 s of time, Dec to 0.1″, the field size to one of the five sizes; a place within SNAP of the
    field size from a field the service already has is that field."""
    p = _rounded(ra, dec, fov)
    tol = p["fov"] / 60.0 * SNAP
    for group in (_WARM, _CENTRES):
        hit = _nearest(group, p, tol)
        if hit is not None:
            return {"ra": hit[0], "dec": hit[1], "fov": p["fov"]}
    return p


def _nearest(group: dict[float, set[tuple[float, float]]], p: dict[str, Any], tol: float) -> tuple[float, float] | None:
    best: tuple[float, float] | None = None
    best_d = tol
    for r, d in group.get(p["fov"], ()):
        dra = ((p["ra"] - r + 540.0) % 360.0 - 180.0) * math.cos(math.radians(0.5 * (p["dec"] + d)))
        sep = math.hypot(dra, p["dec"] - d)
        if sep <= best_d:
            best, best_d = (r, d), sep
    return best


def memory_estimate_mb(fov: float) -> int:
    n = grid_for_fov(0.0, 0.0, fov).n
    return int(400 + 6 * n * n * 4 * 22 / 1e6)  # a detector group plus the band sums and the pass layers


def film_memory_mb(fov: float) -> int:
    """What a film or a film search of a field holds at its peak (every visit's frame and the workers' parts): a 60' film with a film
    search and the field's other searches took the service to its 13 GB limit, so the scheduler counts the big ones at their size and
    starts a second one only when the first leaves room."""
    return 5000 if fov >= 40 else 2500 if fov >= 15 else 1500


def _f16(a: np.ndarray) -> np.ndarray:
    return np.asarray(a, np.float32).astype(np.float16)


def _encode_z(z: np.ndarray) -> np.ndarray:
    out = np.zeros(z.shape, np.uint8)
    ok = np.isfinite(z)
    out[ok] = np.clip(np.round(128 + 12.7 * np.clip(z[ok], -10, 10)), 1, 255).astype(np.uint8)
    return out


def _white_noise(layers: list[np.ndarray]) -> np.ndarray:
    """Model noise of the white frame (the mean of the detector stacks): sqrt(Σσ²)/m where every detector has a value."""
    if not layers:
        raise ValueError("no detector covers the field in every pass")
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
        parts, pm = await ctx.compute(encode_pass, pp, cdet, bands)
        for name, data in parts.items():
            store.write_part(KIND, key, name, data)
        passes_meta.append(pm)
        built.append(pp)
        for part in ("picture", "dets", "bands", "flags"):
            ctx.part_ready(f"{part}_p{k}")
        ctx.phase(f"pass {k + 1}: published", len(passes_meta), None)

    async def on_quick(pp: PassProducts, cdet: tuple[int, ...]) -> None:
        k = pp.index
        store.write_part(KIND, key, f"quick_p{k}.sxp", await ctx.compute(encode_quick, pp, cdet))
        ctx.part_ready(f"quick_p{k}")

    def progress(text: str, done: int | None = None, total: int | None = None) -> None:
        ctx.phase(text, done, total)

    def reading(k: int, n: int, mb: float) -> None:
        ctx.phase(f"reading the exposures: {k} of {n}, {mb:.0f} MB downloaded", k, n)

    ctx.phase("finding the exposures")

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
        reading=reading,
    )
    if not built and b.exposures and len(b.missing) >= len(b.exposures):
        # nothing could be read (the reader failed, not the sky): an error the page shows, never a black field kept as finished
        raise RuntimeError(f"none of the {len(b.exposures)} exposures could be read; ask again in a moment")
    ctx.phase("comparing passes")
    if len(built) >= 2:
        z: dict[str, np.ndarray] = {}
        ref = diff_layers(built[0], b.cdet, bands)
        for pp in built[1:]:
            await ctx.checkpoint()
            z.update(await ctx.compute(diff_pass, ref, diff_layers(pp, b.cdet, bands), pp.index, b.grid.step_arcsec))
        note = {"reference_pass": 0, "encoding": "128 + 12.7 z, clipped at ±10; 0 = no data"}
        store.write_part(KIND, key, "diff.sxp", await ctx.compute(encode, z, note))
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


_SIGNATURES: dict[tuple[str, int], str] = {}


def annotate(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """The field's meta with, for every pass, what the exposures of that pass cover: `detector_status` (D1..D6: the number of exposures and
    "ok" or "no exposure in this pass"), `bands_status` (for each named band: "ok", or "no exposure" when no detector that has exposures in
    the pass covers any window of the band), and `partial` (some detector has no exposure). A pass is often partial (a short season, the
    edge of the survey): the page says "no exposure in D2 this season" instead of showing black. Computed from the counts the meta
    already holds, so no stored field changes."""
    if meta is None:
        return None
    from engine.colour import DETECTOR_RANGES_UM

    windows = {b.key: b.windows for b in load_bands()}
    passes = []
    for p in meta.get("passes", []):
        have = {int(k[1:]): int(v) for k, v in (p.get("detectors") or {}).items() if v}
        status = {
            f"D{d}": {"exposures": have.get(d, 0), "state": "ok" if have.get(d, 0) else "no exposure in this pass"} for d in range(1, 7)
        }
        bands: dict[str, Any] = {}
        for b in meta.get("bands", []):
            wins: tuple[tuple[float, float], ...] = windows.get(b["key"]) or ()
            covered = [d for d, (lo, hi) in DETECTOR_RANGES_UM.items() if d in have and any(w_lo < hi and w_hi > lo for w_lo, w_hi in wins)]
            bands[b["key"]] = {"state": "ok" if covered or not wins else "no exposure", "detectors": covered}
        passes.append({**p, "detector_status": status, "bands_status": bands, "partial": len(have) < 6})
    return {**meta, "passes": passes}


def exposure_signature(index: Index, params: dict[str, Any]) -> str:
    """What the field is built from: its overlapping exposures (a new period changes only the keys of the fields it touches). Kept in
    memory per field and index size, so opening a field again asks nothing of the index."""
    version = getattr(index, "version", None)
    if version is None:  # an index without a version token (tests): no memory
        return _signature(index, params)
    memo = (json.dumps(params, sort_keys=True), int(version()))
    hit = _SIGNATURES.get(memo)
    if hit is not None:
        return hit
    sig = _signature(index, params)
    if len(_SIGNATURES) > 5000:
        _SIGNATURES.clear()
    _SIGNATURES[memo] = sig
    return sig


def _signature(index: Index, params: dict[str, Any]) -> str:
    found = index.overlapping(params["ra"], params["dec"], params["fov"] / 60.0 / 2.0 * 2**0.5)
    import hashlib

    return hashlib.sha256(json.dumps(sorted(e.key for e in found)).encode()).hexdigest()[:16]


# ---- compute of the published parts (module level: these run in worker processes) -------------------------------------------------
def encode_pass(pp: PassProducts, cdet: tuple[int, ...], bands: list[Any]) -> tuple[dict[str, bytes], dict[str, Any]]:
    k = pp.index
    rgb = pp.rgb(cdet)
    sat = pp.sat_share(cdet)
    r, g, b = rgb
    kr, kb = white_balance(r, g, b, np.isfinite(r) & (g > STAR_THRESHOLD) & (sat < 0.5))
    out = {f"picture_p{k}.sxp": encode({"rgb": _f16(rgb), "sat": (np.clip(sat, 0, 1) * 255).astype(np.uint8)}, {"pass": k})}
    dets = np.stack([pp.layer(f"D{d}") for d in range(1, 7)])
    noise = np.stack([pp.noise(f"D{d}") for d in range(1, 7)])
    out[f"dets_p{k}.sxp"] = encode({"dets": _f16(dets), "noise": _f16(noise)}, {"pass": k, "layers": [f"D{d}" for d in range(1, 7)]})
    bl = np.stack([pp.layer(bd.key) for bd in bands])
    out[f"bands_p{k}.sxp"] = encode({"bands": _f16(bl)}, {"pass": k, "layers": [bd.key for bd in bands]})
    names = sorted(pp.census)
    if names:
        fl = np.stack([(pp.census[n] * 255).astype(np.uint8) for n in names])
        out[f"flags_p{k}.sxp"] = encode({"flags": fl}, {"pass": k, "names": names})
    return out, pass_meta(pp, kr, kb)


def encode_quick(pp: PassProducts, cdet: tuple[int, ...]) -> bytes:
    white = pp.white(cdet)
    sat = pp.sat_share(cdet)
    return encode({"white": _f16(white), "sat": (np.clip(sat, 0, 1) * 255).astype(np.uint8)}, {"pass": pp.index})


def diff_layers(pp: PassProducts, cdet: tuple[int, ...], bands: list[Any]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """The layers a difference needs, with their noise (references to the pass's arrays; the white noise is combined later)."""
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {"white": (pp.white(cdet), np.stack([pp.noise(f"D{d}") for d in cdet]))}
    for bd in bands:
        out[bd.key] = (pp.layer(bd.key), pp.noise(bd.key))
    return out


def diff_pass(
    ref: dict[str, tuple[np.ndarray, np.ndarray]], cur: dict[str, tuple[np.ndarray, np.ndarray]], index: int, step_arcsec: float
) -> dict[str, np.ndarray]:
    z = {}
    for name in ref:
        a, sa = ref[name]
        c, sc = cur[name]
        if name == "white":
            sa, sc = _white_noise(list(sa)), _white_noise(list(sc))
        z[f"p{index}_{name}"] = _encode_z(z_map(a, c, sa, sc, step_arcsec))
    return z


def unread(meta: dict[str, Any] | None) -> bool:
    """A kept field none of whose exposures was read (its build failed in the reader): it must be made again."""
    if not meta or meta.get("passes"):
        return False
    n = int(meta.get("exposures") or 0)
    return n > 0 and int(meta.get("not_readable") or 0) >= n
