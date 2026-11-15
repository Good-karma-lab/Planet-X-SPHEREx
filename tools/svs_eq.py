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


def main(argv: list[str]) -> int:
    cmd = argv[1]
    if cmd == "lin":
        stage_lin(Path(argv[2]), Path(argv[3]))
    elif cmd == "pyr":
        stage_pyr(Path(argv[2]))
    elif cmd == "overview":
        stage_overview(Path(argv[2]), Path(argv[3]), Path(argv[4]), argv[5], json.loads(Path(argv[6]).read_text()))
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
