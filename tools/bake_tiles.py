"""Baked globe tiles: WebP tiles of 512 pixels in HEALPix NESTED numbering (the layout of HiPS tiles), served as static files.

    /tiles/<layer>/Norder{o}/Dir{d}/Npix{i}.webp      d = floor(i / 10000) * 10000
    /tiles/manifest.json                                the layers, their orders and credits

Inside a tile the pixel (row r, column c) is the nested pixel  i · 4^9 + spread(r) + 2 · spread(c)  of order o + 9 (rows follow the face's x,
columns its y: what web/src/lib/hips.ts reads). Run it with a Python that has numpy, scipy, pillow, OpenEXR and astropy-healpix:

    tiles-bake/venv/bin/python -I tools/bake_tiles.py exr-to-srgb  SRC.exr WORK/svs.u8          # linear half-float EXR -> sRGB 8-bit, row-major
    tiles-bake/venv/bin/python -I tools/bake_tiles.py equirect     WORK/svs.u8 W H OUT svs 5   # tiles of the deepest order from an equirectangular map
    tiles-bake/venv/bin/python -I tools/bake_tiles.py pyramid      OUT svs 5                   # the shallower orders: four children averaged into one tile
    tiles-bake/venv/bin/python -I tools/bake_tiles.py hips         OUT dss2 CDS/DSS2/color jpg 2.4 16 3 6   # a survey's HiPS tiles, toned, re-encoded to WebP
    tiles-bake/venv/bin/python -I tools/bake_tiles.py manifest     OUT                          # (re)writes OUT/manifest.json from OUT/*/layer.json

`hips` takes the source tiles from IRSA's HiPS root (https://irsa.ipac.caltech.edu/data/hips/), applies the globe's tone to every pixel
(out = max(0, v - black) * gain * 255 / (255 - black), as web/src/screens/globe/sky.ts does) and writes WebP; the orders above the
shallowest source order are made by `pyramid`.

The equirectangular map of the NASA SVS star map has RA 0 at the centre, RA increasing to the left, north up (checked on Sirius, Vega and Arcturus).
The bake is meant to run at low priority (`nice -n 15`).
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

TILE = 512
SUB_ORDER = 9  # a tile holds 4^9 = 512 x 512 pixels of order o + 9
WEBP_QUALITY = 82


def spread(v: np.ndarray) -> np.ndarray:
    """Bits of v spread to the even positions: ... b2 b1 b0 -> ... b2 0 b1 0 b0 (for up to 16 bits)."""
    v = v.astype(np.int64) & 0xFFFF
    v = (v | (v << 8)) & 0x00FF00FF
    v = (v | (v << 4)) & 0x0F0F0F0F
    v = (v | (v << 2)) & 0x33333333
    v = (v | (v << 1)) & 0x55555555
    return v


def offsets() -> np.ndarray:
    """The nested offset inside a tile of every (row, column): spread(row) + 2 * spread(column)."""
    r = np.arange(TILE)
    return spread(r)[:, None] + 2 * spread(r)[None, :]


def tile_path(root: Path, layer: str, order: int, i: int) -> Path:
    return root / layer / f"Norder{order}" / f"Dir{i // 10000 * 10000}" / f"Npix{i}.webp"


def save_webp(path: Path, rgb: np.ndarray) -> int:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    Image.fromarray(rgb, "RGB").save(tmp, "WEBP", quality=WEBP_QUALITY, method=4)
    tmp.replace(path)
    return path.stat().st_size


def exr_to_srgb(src: Path, dst: Path) -> tuple[int, int]:
    """A linear half-float RGB EXR written as 8-bit sRGB rows (the exact sRGB transfer function, no other tone) to `dst`."""
    import OpenEXR

    f = OpenEXR.File(str(src))
    ch = f.channels()
    pix = ch["RGB"].pixels if "RGB" in ch else np.stack([ch[c].pixels for c in "RGB"], -1)
    h, w, _ = pix.shape
    out = np.memmap(dst, dtype=np.uint8, mode="w+", shape=(h, w, 3))
    step = 1024
    for y in range(0, h, step):
        lin = np.clip(pix[y : y + step].astype(np.float32), 0.0, 1.0)
        srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1.0 / 2.4) - 0.055)
        out[y : y + step] = np.clip(np.rint(srgb * 255.0), 0, 255).astype(np.uint8)
    out.flush()
    return w, h


_STATE: dict[str, Any] = {}


def _init_equirect(path: str, w: int, h: int, order: int, root: str, layer: str) -> None:
    _STATE.update(img=np.memmap(path, dtype=np.uint8, mode="r", shape=(h, w, 3)), w=w, h=h, order=order, root=Path(root), layer=layer, off=offsets())


def _bake_equirect_tile(i: int) -> int:
    from astropy_healpix import healpix_to_lonlat
    from scipy.ndimage import map_coordinates

    st = _STATE
    order = st["order"]
    pix = i * (4**SUB_ORDER) + st["off"]
    lon, lat = healpix_to_lonlat(pix.ravel(), 2 ** (order + SUB_ORDER), order="nested")
    ra, dec = np.degrees(lon.value), np.degrees(lat.value)
    w, h = st["w"], st["h"]
    x = ((0.5 - ra / 360.0) % 1.0) * w - 0.5  # RA 0 at the centre, increasing to the left
    y = np.clip((0.5 - dec / 180.0) * h - 0.5, 0.0, h - 1.0)
    if float(np.abs(dec).max()) > 80.0:  # a polar tile reaches round the whole circle of RA: the map's full width
        x0, x1 = 0, w
        x = np.clip(x, 0.0, w - 1.0)
    else:
        xc = float(np.median(x))
        x = ((x - xc + w / 2.0) % w) - w / 2.0 + xc  # one side of the seam: the tile's own stretch of the map
        x0, x1 = int(np.floor(x.min())) - 1, int(np.ceil(x.max())) + 2
    y0, y1 = max(0, int(np.floor(y.min())) - 1), min(h, int(np.ceil(y.max())) + 2)
    cols = np.arange(x0, x1) % w
    window = np.ascontiguousarray(st["img"][y0:y1][:, cols])  # only this window of the 6 GB map is read and copied
    out = np.empty((TILE * TILE, 3), np.uint8)
    for c in range(3):
        v = map_coordinates(window[:, :, c], [y - y0, x - x0], order=1, mode="nearest")
        out[:, c] = np.clip(np.rint(v), 0, 255)
    return save_webp(tile_path(st["root"], st["layer"], order, i), out.reshape(TILE, TILE, 3))


def bake_equirect(src: Path, w: int, h: int, root: Path, layer: str, order: int, workers: int) -> None:
    n = 12 * 4**order
    todo = [i for i in range(n) if not tile_path(root, layer, order, i).exists()]
    print(f"order {order}: {n} tiles, {len(todo)} to bake, {workers} workers", flush=True)
    t0, total, done = time.time(), 0, 0
    with mp.Pool(workers, _init_equirect, (str(src), w, h, order, str(root), layer)) as pool:
        for size in pool.imap_unordered(_bake_equirect_tile, todo, chunksize=4):
            done += 1
            total += size
            if done % 500 == 0:
                print(f"  {done}/{len(todo)} tiles, {total / 1e6:.0f} MB, {time.time() - t0:.0f} s", flush=True)
    print(f"order {order} done: {done} tiles in {time.time() - t0:.0f} s, {total / 1e6:.0f} MB", flush=True)


def _load(root: Path, layer: str, order: int, i: int) -> np.ndarray:
    from PIL import Image

    with Image.open(tile_path(root, layer, order, i)) as im:
        return np.asarray(im.convert("RGB"))


def _pyramid_tile(args: tuple[str, str, int, int]) -> int:
    root_s, layer, order, i = args
    root = Path(root_s)
    mosaic = np.zeros((2 * TILE, 2 * TILE, 3), np.uint16)
    for q in range(4):  # child 4i + q: bit 0 selects the half of the rows, bit 1 the half of the columns
        child = tile_path(root, layer, order + 1, 4 * i + q)
        if child.exists():
            mosaic[(q & 1) * TILE : (q & 1) * TILE + TILE, (q >> 1) * TILE : (q >> 1) * TILE + TILE] = _load(root, layer, order + 1, 4 * i + q)
    small = (mosaic.reshape(TILE, 2, TILE, 2, 3).sum(axis=(1, 3)) + 2) // 4
    return save_webp(tile_path(root, layer, order, i), small.astype(np.uint8))


def pyramid(root: Path, layer: str, max_order: int, workers: int) -> None:
    for order in range(max_order - 1, -1, -1):
        n = 12 * 4**order
        t0 = time.time()
        with mp.Pool(workers) as pool:
            sizes = pool.map(_pyramid_tile, [(str(root), layer, order, i) for i in range(n)], chunksize=8)
        print(f"order {order}: {n} tiles, {sum(sizes) / 1e6:.0f} MB, {time.time() - t0:.0f} s", flush=True)


def _fetch_hips_tile(args: tuple[str, str, str, int, int, float, float, str]) -> int:
    import io
    import ssl
    import urllib.error
    import urllib.request

    import certifi
    from PIL import Image

    root_s, layer, path, order, i, gain, black, ext = args
    dest = tile_path(Path(root_s), layer, order, i)
    if dest.exists():
        return -1
    url = f"https://irsa.ipac.caltech.edu/data/hips/{path}/Norder{order}/Dir{i // 10000 * 10000}/Npix{i}.{ext}"
    raw = b""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "spherex-tile-bake/1"}),
                timeout=40,
                context=ssl.create_default_context(cafile=certifi.where()),
            ) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 0  # outside the survey: no tile, the server answers 404 for it too
            time.sleep(1.5 * (attempt + 1))
        except OSError:
            time.sleep(1.5 * (attempt + 1))
    if not raw:
        return -2
    with Image.open(io.BytesIO(raw)) as im:
        rgb = np.asarray(im.convert("RGB")).astype(np.float32)
    if rgb.shape[0] != TILE:  # a 256 pixel source is smoothly enlarged to the 512 pixel tile
        from PIL import Image as _I

        rgb = np.asarray(_I.fromarray(rgb.astype(np.uint8)).resize((TILE, TILE), _I.BICUBIC)).astype(np.float32)
    g = gain * 255.0 / (255.0 - black)
    toned = np.clip(np.maximum(0.0, rgb - black) * g, 0, 255).astype(np.uint8)
    return save_webp(dest, toned)


def bake_hips(root: Path, layer: str, path: str, ext: str, gain: float, black: float, omin: int, omax: int, threads: int) -> None:
    from concurrent.futures import ThreadPoolExecutor

    for order in range(omin, omax + 1):
        n = 12 * 4**order
        t0, total, made, missing, failed = time.time(), 0, 0, 0, 0
        jobs = [(str(root), layer, path, order, i, gain, black, ext) for i in range(n)]
        with ThreadPoolExecutor(threads) as ex:
            for k, size in enumerate(ex.map(_fetch_hips_tile, jobs), 1):
                if size > 0:
                    made += 1
                    total += size
                elif size == 0:
                    missing += 1
                elif size == -2:
                    failed += 1
                if k % 2000 == 0:
                    print(f"  order {order}: {k}/{n} ({made} written, {missing} outside, {failed} failed), {time.time() - t0:.0f} s", flush=True)
        print(f"order {order}: {n} asked, {made} written ({total / 1e6:.0f} MB), {missing} outside the survey, {failed} failed, {time.time() - t0:.0f} s", flush=True)


def write_layer_json(root: Path, layer: str, info: dict[str, Any]) -> None:
    (root / layer).mkdir(parents=True, exist_ok=True)
    (root / layer / "layer.json").write_text(json.dumps(info, indent=1))


def write_manifest(root: Path) -> None:
    layers: dict[str, Any] = {}
    for lj in sorted(root.glob("*/layer.json")):
        info = json.loads(lj.read_text())
        folder = lj.parent.name  # a versioned folder (svs-v1): the immutable cache never serves stale tiles after a re-bake
        lid = info.get("id", folder)
        tiles = sum(1 for _ in (root / folder).glob("Norder*/**/*.webp"))
        size = sum(p.stat().st_size for p in (root / folder).glob("Norder*/**/*.webp"))
        layers[lid] = {
            "label": info["label"],
            "credit": info["credit"],
            "frame": info.get("frame", "equatorial"),
            "size": TILE,
            "format": "webp",
            "min": info.get("min", 0),
            "max": info["max"],
            "url": f"/tiles/{folder}/Norder{{o}}/Dir{{d}}/Npix{{i}}.webp",
            "allsky": None,
            "gain": info.get("gain", 1),
            "black": info.get("black", 0),
            "tiles": tiles,
            "bytes": size,
        }
    (root / "manifest.json").write_text(json.dumps({"v": 1, "layers": layers}, indent=1))
    print(json.dumps({k: (v["max"], v["tiles"], round(v["bytes"] / 1e6)) for k, v in layers.items()}))


def main(argv: list[str]) -> int:
    cmd = argv[1]
    workers = int(os.environ.get("BAKE_WORKERS", "6"))
    if cmd == "exr-to-srgb":
        w, h = exr_to_srgb(Path(argv[2]), Path(argv[3]))
        print(f"wrote {argv[3]}: {w} x {h} x 3 uint8")
    elif cmd == "equirect":
        src, w, h, root, layer, order = Path(argv[2]), int(argv[3]), int(argv[4]), Path(argv[5]), argv[6], int(argv[7])
        bake_equirect(src, w, h, root, layer, order, workers)
    elif cmd == "hips":
        bake_hips(Path(argv[2]), argv[3], argv[4], argv[5], float(argv[6]), float(argv[7]), int(argv[8]), int(argv[9]), int(os.environ.get("BAKE_THREADS", "12")))
    elif cmd == "pyramid":
        pyramid(Path(argv[2]), argv[3], int(argv[4]), workers)
    elif cmd == "manifest":
        write_manifest(Path(argv[2]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
