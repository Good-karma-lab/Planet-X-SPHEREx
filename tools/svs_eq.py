# ruff: noqa: E501
"""The NASA SVS star map as an equirectangular (plate carree) pyramid of 512 px WebP tiles, built in linear light.

    stage lin   SRC.exr WORK     the source (linear half-float, RA 0 at the centre increasing to the left) as WORK/L7.f16 in the layer's
                                 orientation: column x = RA / 360 (RA 0h at the left edge, increasing to the right), row y = (90 - Dec) / 180
    stage pyr   WORK             the shallower levels, each the 2 x 2 mean (linear light) of the one below: WORK/L6.f16 ... L1.f16
    stage tiles WORK OUT LAYER LO HI CFG.json   the tiles of the levels LO..HI, tone-mapped from linear light and then quantised to 8 bit

Level l has 2^l columns and 2^(l-1) rows of 512 px tiles (level 7 = 65536 x 32768, the source's own size); the tile file is
OUT/LAYER/L{l}/{x}_{y}.webp (x from the left, y from the top). Run with the tiles-bake venv: python -I tools/svs_eq.py ...
"""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np

TILE = 512
MAXL = 7


def shape_of(level: int) -> tuple[int, int, int]:
    return (TILE * 2 ** (level - 1), TILE * 2**level, 3)


def stage_lin(src: Path, work: Path) -> None:
    import OpenEXR

    work.mkdir(parents=True, exist_ok=True)
    f = OpenEXR.File(str(src))
    ch = f.channels()
    pix = ch["RGB"].pixels if "RGB" in ch else np.stack([ch[c].pixels for c in "RGB"], -1)
    h, w, _ = pix.shape
    assert (h, w) == shape_of(MAXL)[:2], (h, w)
    out = np.memmap(work / "L7.f16", dtype=np.float16, mode="w+", shape=(h, w, 3))
    cols = (w // 2 - 1 - np.arange(w)) % w  # target column x holds the source column that has the same RA
    t0 = time.time()
    for y in range(0, h, 256):
        out[y : y + 256] = pix[y : y + 256][:, cols].astype(np.float16)
        if (y // 256) % 16 == 0:
            print(f"lin {y}/{h} {time.time() - t0:.0f} s", flush=True)
    out.flush()
    print("LIN_DONE", flush=True)


def stage_pyr(work: Path) -> None:
    for level in range(MAXL - 1, 0, -1):
        src = np.memmap(work / f"L{level + 1}.f16", dtype=np.float16, mode="r", shape=shape_of(level + 1))
        h, w, _ = shape_of(level)
        dst = np.memmap(work / f"L{level}.f16", dtype=np.float16, mode="w+", shape=(h, w, 3))
        step = 512
        for y in range(0, h, step):
            blk = src[2 * y : 2 * (y + step)].astype(np.float32)
            dst[y : y + step] = (blk.reshape(-1, 2, w, 2, 3).mean(axis=(1, 3))).astype(np.float16)
        dst.flush()
        print(f"pyr level {level} done", flush=True)
    print("PYR_DONE", flush=True)


def srgb_encode(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * np.power(np.maximum(v, 0.0), 1.0 / 2.4) - 0.055)


def tone(lin: np.ndarray, cfg: dict, seed: int = 0) -> np.ndarray:
    """Linear light (float32; the 64k map in its own units) -> 8-bit display values. The light is brought to the units of NASA's 4k map
    (x k: the 64k map, averaged to 4k, is about 15 times fainter than that map), rolled off softly at the top (1 - exp(-x): a bright star keeps
    its profile instead of a flat disc, a faint one is untouched), the colour kept (the three channels scaled together), encoded as sRGB and
    quantised with a triangular dither of one step (the faint Milky Way would otherwise be a staircase of a few grey levels)."""
    k = float(cfg["k"])
    lum = np.maximum(lin @ np.array([0.2126, 0.7152, 0.0722], np.float32), 1e-9)
    x = k * lum
    out_l = 1.0 - np.exp(-x) if cfg.get("shoulder", True) else np.minimum(x, 1.0)
    rgb = lin * (out_l / lum)[..., None]
    over = rgb.max(axis=-1, keepdims=True)
    rgb = np.where(over > 1.0, rgb / np.maximum(over, 1e-9), rgb)
    enc = srgb_encode(np.clip(rgb, 0.0, 1.0)) * 255.0
    rng = np.random.default_rng(seed)
    noise = rng.random(enc.shape, dtype=np.float32) - rng.random(enc.shape, dtype=np.float32)  # triangular, +-1 step
    return np.clip(np.rint(enc + noise), 0, 255).astype(np.uint8)


_S: dict = {}


def _init(work: str, level: int, out: str, layer: str, cfg: dict, q: int) -> None:
    _S.update(
        img=np.memmap(Path(work) / f"L{level}.f16", dtype=np.float16, mode="r", shape=shape_of(level)),
        level=level,
        out=Path(out),
        layer=layer,
        cfg=cfg,
        q=q,
    )


def _tile(xy: tuple[int, int]) -> int:
    from PIL import Image

    x, y = xy
    s = _S
    dest = s["out"] / s["layer"] / f"L{s['level']}" / f"{x}_{y}.webp"
    if dest.exists():
        return 0
    blk = np.asarray(s["img"][y * TILE : (y + 1) * TILE, x * TILE : (x + 1) * TILE]).astype(np.float32)
    rgb = tone(np.maximum(blk, 0.0), s["cfg"], x * 1000 + y)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    Image.fromarray(rgb, "RGB").save(tmp, "WEBP", quality=s["q"], method=4)
    tmp.replace(dest)
    return dest.stat().st_size


def stage_tiles(work: Path, out: Path, layer: str, lo: int, hi: int, cfg: dict, workers: int) -> None:
    for level in range(lo, hi + 1):
        cols, rows = 2**level, 2 ** (level - 1)
        jobs = [(x, y) for y in range(rows) for x in range(cols)]
        t0, total = time.time(), 0
        with mp.Pool(workers, _init, (str(work), level, str(out), layer, cfg, int(cfg.get("quality", 92)))) as pool:
            for k, sz in enumerate(pool.imap_unordered(_tile, jobs, chunksize=8), 1):
                total += sz
                if k % 2000 == 0:
                    print(f"  level {level}: {k}/{len(jobs)} {total / 1e6:.0f} MB {time.time() - t0:.0f} s", flush=True)
        print(f"level {level}: {len(jobs)} tiles, {total / 1e6:.0f} MB, {time.time() - t0:.0f} s", flush=True)
    print("TILES_DONE", flush=True)


def stage_overview(exr4k: Path, work: Path, out: Path, layer: str, cfg: dict) -> None:
    """The overview levels (2, 3), the whole-sky base image and the 4k and 8k JPEGs. NASA's own 4k map is the overview: its stars are drawn
    large (the 64k map averaged to 4k has them as faint specks, about 15 times fainter overall), and its light is encoded as plain sRGB,
    so levels 2 and 3 are that map (RA turned into the layer's orientation), dithered. Deeper levels come from the 64k map."""
    import OpenEXR
    from PIL import Image

    f = OpenEXR.File(str(exr4k))
    ch = f.channels()
    pix = ch["RGB"].pixels if "RGB" in ch else np.stack([ch[c].pixels for c in "RGB"], -1)
    lin = np.clip(pix.astype(np.float32), 0.0, 1.0)
    h, w, _ = lin.shape
    assert (h, w) == shape_of(3)[:2]
    lin = lin[:, (w // 2 - 1 - np.arange(w)) % w]
    rng = np.random.default_rng(3)

    def encode(a: np.ndarray) -> np.ndarray:
        enc = srgb_encode(a) * 255.0
        noise = rng.random(enc.shape, dtype=np.float32) - rng.random(enc.shape, dtype=np.float32)
        return np.clip(np.rint(enc + noise), 0, 255).astype(np.uint8)

    q = int(cfg.get("quality", 92))
    lev3 = encode(lin)
    lev2 = encode(lin.reshape(h // 2, 2, w // 2, 2, 3).mean(axis=(1, 3)))
    for level, img in ((3, lev3), (2, lev2)):
        for y in range(img.shape[0] // TILE):
            for x in range(img.shape[1] // TILE):
                d = out / layer / f"L{level}" / f"{x}_{y}.webp"
                d.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(img[y * TILE : (y + 1) * TILE, x * TILE : (x + 1) * TILE], "RGB").save(d, "WEBP", quality=q, method=4)
    Image.fromarray(lev3, "RGB").save(out / layer / "base.webp", "WEBP", quality=q, method=4)
    Image.fromarray(lev3, "RGB").save(out / layer / "sky_4k.jpg", "JPEG", quality=94, optimize=True)
    l4 = np.asarray(np.memmap(work / "L4.f16", dtype=np.float16, mode="r", shape=shape_of(4))).astype(np.float32)
    Image.fromarray(tone(np.maximum(l4, 0.0), cfg, 4), "RGB").save(out / layer / "sky_8k.jpg", "JPEG", quality=92, optimize=True)
    print("OVERVIEW_DONE", flush=True)


def _hp_offsets() -> np.ndarray:
    r = np.arange(TILE)

    def spread(v: np.ndarray) -> np.ndarray:
        v = v.astype(np.int64) & 0xFFFF
        v = (v | (v << 8)) & 0x00FF00FF
        v = (v | (v << 4)) & 0x0F0F0F0F
        v = (v | (v << 2)) & 0x33333333
        v = (v | (v << 1)) & 0x55555555
        return v

    return spread(r)[:, None] + 2 * spread(r)[None, :]


# the equirect level whose pixel is about as large as the HEALPix pixel of each order (6.4 arcsec x 2^(6 - order)): the map is sampled
# from the level that is already the area mean at that scale, so a coarse order never aliases the fine map
HP_LEVEL = {0: 3, 1: 4, 2: 5, 3: 6, 4: 7, 5: 7, 6: 7}
HP_NASA_MAX_ORDER = 2  # orders up to this one are NASA's 4k map (the overview look: big bright stars); finer ones come from the 64k map

_H: dict = {}


def _hp_init(work: str, nasa: str, order: int, out: str, layer: str, cfg: dict) -> None:
    lvl = HP_LEVEL[order]
    _H.update(
        order=order,
        out=Path(out),
        layer=layer,
        cfg=cfg,
        off=_hp_offsets(),
        nasa=None if order > HP_NASA_MAX_ORDER else np.load(nasa),
        img=None
        if order <= HP_NASA_MAX_ORDER and not cfg.get("polar_mix")
        else np.memmap(Path(work) / f"L{lvl}.f16", dtype=np.float16, mode="r", shape=shape_of(lvl)),
    )


def _hp_tile(i: int) -> int:
    from astropy import units as u
    from astropy_healpix import healpix_to_lonlat
    from PIL import Image

    h = _H
    order = h["order"]
    dest = h["out"] / h["layer"] / f"Norder{order}" / f"Dir{i // 10000 * 10000}" / f"Npix{i}.webp"
    if dest.exists():
        return 0
    idx = i * 4**9 + h["off"].ravel()
    lon, lat = healpix_to_lonlat(idx, 2 ** (order + 9), order="nested")
    ra, dec = lon.to_value(u.deg), lat.to_value(u.deg)

    def sample(src: np.ndarray) -> np.ndarray:
        hh, ww = src.shape[0], src.shape[1]
        x = ra / 360.0 * ww - 0.5
        y = (90.0 - dec) / 180.0 * hh - 0.5
        x0 = np.floor(x).astype(np.int64)
        y0 = np.floor(y).astype(np.int64)
        fx = (x - x0).astype(np.float32)[:, None]
        fy = (y - y0).astype(np.float32)[:, None]
        xa, xb = x0 % ww, (x0 + 1) % ww  # RA wraps
        ya, yb = (
            np.clip(y0, 0, hh - 1),
            np.clip(y0 + 1, 0, hh - 1),
        )  # the poles: the rows are clamped (the row at the pole is the mean over RA)
        return (
            src[ya, xa].astype(np.float32) * (1 - fx) * (1 - fy)
            + src[ya, xb].astype(np.float32) * fx * (1 - fy)
            + src[yb, xa].astype(np.float32) * (1 - fx) * fy
            + src[yb, xb].astype(np.float32) * fx * fy
        ).reshape(TILE, TILE, 3)

    def nasa_rgb(lin: np.ndarray) -> np.ndarray:
        enc = srgb_encode(np.clip(lin, 0.0, 1.0)) * 255.0
        rng = np.random.default_rng(i)
        noise = rng.random(enc.shape, dtype=np.float32) - rng.random(enc.shape, dtype=np.float32)
        return enc + noise

    if h["nasa"] is None:
        rgb = tone(np.maximum(sample(h["img"]), 0.0), h["cfg"], order * 100003 + i)
    elif h["cfg"].get("polar_mix") and float(np.abs(dec).max()) > float(h["cfg"]["polar_mix"]["start"]):
        # NASA's 4k stars are round in the image, so squeezed along RA on the sphere near a pole (radial streaks): the polar caps of the
        # overview orders mix over to the 64k map (its stars are round on the sphere), the mix growing from `start` to `full` degrees
        pm = h["cfg"]["polar_mix"]
        w = np.clip((np.abs(dec) - float(pm["start"])) / (float(pm["full"]) - float(pm["start"])), 0.0, 1.0)
        w = (w * w * (3 - 2 * w)).reshape(TILE, TILE, 1).astype(np.float32)
        a_ = nasa_rgb(sample(h["nasa"]))
        b_ = tone(np.maximum(sample(h["img"]), 0.0), h["cfg"], order * 100003 + i).astype(np.float32)
        rgb = np.clip(np.rint(a_ * (1 - w) + b_ * w), 0, 255).astype(np.uint8)
    else:
        rgb = np.clip(np.rint(nasa_rgb(sample(h["nasa"]))), 0, 255).astype(np.uint8)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    Image.fromarray(rgb, "RGB").save(tmp, "WEBP", quality=int(h["cfg"].get("quality", 92)), method=4)
    tmp.replace(dest)
    return dest.stat().st_size


def _polar_blurred(nasa: Path, work: Path, pb: dict) -> Path:
    """NASA's 4k map draws its stars round in the image, so on the sphere they are squeezed along RA by cos(dec): radial streaks at the poles.
    Each row is blurred along RA by sigma0 * tan(dec) (flux kept) so a star is round on the sphere again; the blur fades in between
    `start` and `full` degrees of declination (the diffuse light keeps its value: the blur conserves it)."""
    from scipy.ndimage import gaussian_filter1d

    out = work / "nasa4k_lin_polar.npy"
    lin = np.load(nasa)
    h = lin.shape[0]
    s0, start, full = float(pb["sigma"]), float(pb["start"]), float(pb["full"])
    res = lin.copy()
    for y in range(h):
        dec = abs(90.0 - (y + 0.5) / h * 180.0)
        ramp = float(np.clip((dec - start) / (full - start), 0.0, 1.0))
        ramp = ramp * ramp * (3 - 2 * ramp)
        sig = s0 * math.tan(math.radians(min(dec, 89.9))) * ramp
        if sig > 0.3:
            res[y] = gaussian_filter1d(lin[y], sig, axis=0, mode="wrap")
    np.save(out, res)
    return out


def stage_healpix(work: Path, exr4k: Path, out: Path, layer: str, lo: int, hi: int, cfg: dict, workers: int) -> None:
    """The same tone-mapped star map as a HEALPix NESTED pyramid of 512 px WebP tiles (equal area: no pole problem). Orders <= HP_NASA_MAX_ORDER
    are NASA's 4k map, finer ones the 64k map in linear light (x k, soft top, dither), exactly as the equirect pyramid."""
    import OpenEXR

    nasa = work / "nasa4k_lin.npy"
    if not nasa.exists():
        f = OpenEXR.File(str(exr4k))
        ch = f.channels()
        pix = ch["RGB"].pixels if "RGB" in ch else np.stack([ch[c].pixels for c in "RGB"], -1)
        lin = np.clip(pix.astype(np.float32), 0.0, 1.0)
        w = lin.shape[1]
        np.save(nasa, lin[:, (w // 2 - 1 - np.arange(w)) % w])  # RA turned to the layer's orientation (0 h at the left, increasing right)
    pb = cfg.get("polar_blur")
    if pb:
        nasa = _polar_blurred(nasa, work, pb)
    for order in range(lo, hi + 1):
        n = 12 * 4**order
        t0, total = time.time(), 0
        with mp.Pool(workers, _hp_init, (str(work), str(nasa), order, str(out), layer, cfg)) as pool:
            for k, sz in enumerate(pool.imap_unordered(_hp_tile, range(n), chunksize=8), 1):
                total += sz or 0
                if k % 4000 == 0:
                    print(f"  order {order}: {k}/{n} {total / 1e6:.0f} MB {time.time() - t0:.0f} s", flush=True)
        print(f"order {order}: {n} tiles, {total / 1e6:.0f} MB, {time.time() - t0:.0f} s", flush=True)
    print("HEALPIX_DONE", flush=True)


STAR_ABOVE = 0.05  # linear light


def _pool_tile(args: tuple[str, str, int, int, float]) -> int:
    from PIL import Image

    root_s, layer, order, i, w = args
    root = Path(root_s) / layer
    dest = root / f"Norder{order}" / f"Dir{i // 10000 * 10000}" / f"Npix{i}.webp"
    if dest.exists():
        return 0
    mosaic = np.zeros((2 * TILE, 2 * TILE, 3), np.float32)
    for q in range(4):  # child 4i + q: bit 0 selects the half of the rows, bit 1 the half of the columns
        c = 4 * i + q
        p = root / f"Norder{order + 1}" / f"Dir{c // 10000 * 10000}" / f"Npix{c}.webp"
        if p.exists():
            a = np.asarray(Image.open(p).convert("RGB")).astype(np.float32) / 255.0
            mosaic[(q & 1) * TILE : (q & 1) * TILE + TILE, (q >> 1) * TILE : (q >> 1) * TILE + TILE] = np.where(
                a <= 0.04045, a / 12.92, np.power((a + 0.055) / 1.055, 2.4)
            )
    blk = mosaic.reshape(TILE, 2, TILE, 2, 3)
    mean, peak = blk.mean(axis=(1, 3)), blk.max(axis=(1, 3))
    # the area mean, plus `w` of what a child stands out above that mean by more than STAR_ABOVE (a star: it must not dilute over six
    # orders; the sky's grain and the diffuse light stand out by less and are left at their mean, so the background does not rise)
    lin = mean + w * np.maximum(peak - mean - STAR_ABOVE, 0.0)
    rgb = np.clip(np.rint(srgb_encode(np.clip(lin, 0.0, 1.0)) * 255.0), 0, 255).astype(np.uint8)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    Image.fromarray(rgb, "RGB").save(tmp, "WEBP", quality=92, method=4)
    tmp.replace(dest)
    return dest.stat().st_size


def stage_pyramid(root: Path, layer: str, top: int, weight: float, workers: int) -> None:
    """Every coarser order of a HEALPix layer from the next finer one (the standard HiPS way: the four children area-averaged in linear light,
    `weight` of the way to their maximum so the stars stay), starting from the order `top`: no coarse order samples the equirect map."""
    for order in range(top - 1, -1, -1):
        n = 12 * 4**order
        t0 = time.time()
        with mp.Pool(workers) as pool:
            sizes = pool.map(_pool_tile, [(str(root), layer, order, i, weight) for i in range(n)], chunksize=16)
        print(f"order {order}: {n} tiles, {sum(sizes) / 1e6:.0f} MB, {time.time() - t0:.0f} s", flush=True)
    print("POOL_DONE", flush=True)


def _mix_tile(args: tuple[str, str, str, int, int, float, float]) -> int:
    from astropy import units as u
    from astropy_healpix import healpix_to_lonlat
    from PIL import Image

    nasa_root, pool_root, out_root, order, i, start, full = args
    rel = f"Norder{order}/Dir{i // 10000 * 10000}/Npix{i}.webp"
    idx = i * 4**9 + _hp_offsets().ravel()
    _, lat = healpix_to_lonlat(idx, 2 ** (order + 9), order="nested")
    dec = np.abs(lat.to_value(u.deg)).reshape(TILE, TILE, 1)
    w = np.clip((dec - start) / (full - start), 0.0, 1.0)
    w = (w * w * (3 - 2 * w)).astype(np.float32)
    a = np.asarray(Image.open(Path(nasa_root) / rel).convert("RGB")).astype(np.float32)
    b = np.asarray(Image.open(Path(pool_root) / rel).convert("RGB")).astype(np.float32)
    rgb = np.clip(np.rint(a * (1 - w) + b * w), 0, 255).astype(np.uint8)
    dest = Path(out_root) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, "RGB").save(dest, "WEBP", quality=92, method=4)
    return dest.stat().st_size


def stage_mix(nasa_root: Path, pool_root: Path, out_root: Path, hi_order: int, start: float, full: float, workers: int) -> None:
    """The overview orders (0..hi_order): NASA's 4k look away from the poles, the pooled pyramid (no squeezed stars) towards them, the mix
    growing from `start` to `full` degrees of |declination|."""
    for order in range(0, hi_order + 1):
        n = 12 * 4**order
        with mp.Pool(workers) as pool:
            sizes = pool.map(
                _mix_tile, [(str(nasa_root), str(pool_root), str(out_root), order, i, start, full) for i in range(n)], chunksize=8
            )
        print(f"order {order}: {n} tiles, {sum(sizes) / 1e6:.0f} MB", flush=True)
    print("MIX_DONE", flush=True)


def main(argv: list[str]) -> int:
    cmd = argv[1]
    if cmd == "lin":
        stage_lin(Path(argv[2]), Path(argv[3]))
    elif cmd == "pyr":
        stage_pyr(Path(argv[2]))
    elif cmd == "overview":
        stage_overview(Path(argv[2]), Path(argv[3]), Path(argv[4]), argv[5], json.loads(Path(argv[6]).read_text()))
    elif cmd == "healpix":
        stage_healpix(
            Path(argv[2]),
            Path(argv[3]),
            Path(argv[4]),
            argv[5],
            int(argv[6]),
            int(argv[7]),
            json.loads(Path(argv[8]).read_text()),
            int(__import__("os").environ.get("BAKE_WORKERS", "6")),
        )
    elif cmd == "mix":
        stage_mix(
            Path(argv[2]),
            Path(argv[3]),
            Path(argv[4]),
            int(argv[5]),
            float(argv[6]),
            float(argv[7]),
            int(__import__("os").environ.get("BAKE_WORKERS", "6")),
        )
    elif cmd == "pyramid":
        stage_pyramid(Path(argv[2]), argv[3], int(argv[4]), float(argv[5]), int(__import__("os").environ.get("BAKE_WORKERS", "6")))
    elif cmd == "tiles":
        stage_tiles(
            Path(argv[2]),
            Path(argv[3]),
            argv[4],
            int(argv[5]),
            int(argv[6]),
            json.loads(Path(argv[7]).read_text()),
            int(__import__("os").environ.get("BAKE_WORKERS", "6")),
        )
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
