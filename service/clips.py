"""The time-lapse clip of a finding: a small looping picture of the place, one frame per pass, the object ringed.

A clip is cut from the same stacked passes the lab shows (the `picture_p<k>` parts of a field: R, G, B and the saturated share) and drawn
with the lab's own display law (web/src/viewer/renderer.ts, the colour picture: white balance on the field's stars, each channel against
the noise of the picture, the fixed asinh law for bright stars, colour boost 2.4, saturated cores white). Every frame is the same window
of the same grid, so the background stands still and only what really moved between the passes moves.

Where the frames come from, in this order:
  1. a finished field of the main store that holds the whole window (the finest grid first, then the smallest field);
  2. for a slow mover that only a 60′ field (3.075″ grid) holds, and for a place no finished field holds: a 5′ field centred on the
     object, built by the ordinary field job into the clips' OWN store (`SKY_CLIPS`, default `<store>/clips`), never into the main store.

    <SKY_CLIPS>/<VERSION>/<id[:2]>/<id>.webp   the clip (animated WebP, loops for ever)
    <SKY_CLIPS>/<VERSION>/<id[:2]>/<id>.json   what it shows: field, window, passes with dates, ring per frame, render time, bytes
    <SKY_CLIPS>/products/…                     the 5′ fields built for clips (a product store of its own)

A clip is rendered once; a new VERSION (a change of the picture) starts a new folder and the old one is left to be deleted by hand.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import maximum_filter

from engine.grid import NATIVE_PIXEL_ARCSEC, STEP_FINE_ARCSEC, Grid
from service.products.store import Store
from service.products.sxp import decode
from service.storage import atomic_write_bytes

VERSION = "c2"
WINDOW_ARCMIN = 3.0  # the side of the clip on the sky
SIZE = 320  # the side of the clip in pixels
FRAME_MS = 1000  # one pass on screen
LAST_HOLD_MS = 1800  # the last pass stays a little longer before the clip starts again
QUALITY = 82
CLIP_FOV = 5.0  # the field built for a clip when no finished field will do
MAX_BUILDS = 2  # fields being built for clips at once (running or queued); further clips wait their turn and are asked for again
LABELS_MIN_SIZE = 200  # a clip smaller than this carries no dates and no width (they would run into each other)
RING_ARCSEC = 15.0  # radius of the ring round the object
MOVING = ("mover", "browndwarf", "peak")  # kinds whose motion between passes is a few arcseconds: they want the fine grid
FIELD = "field"

# the lab's display law (web/src/viewer/law.ts and renderer.ts)
LO, HI, SOFT, BOOST = -0.05, 400.0, 0.4, 2.4
BN_KNEE, BN_FULL = 4.0, 20000.0
NO_DATA = (0.0, 0.0, 0.0)

F = NDArray[np.floating[Any]]


class ClipUnavailable(Exception):  # noqa: N818  (a state of the clip, not a programming error)
    """No clip can be made: the reason is the message (shown on the card)."""


class ClipPending(Exception):  # noqa: N818
    """The field the clip needs is being built: ask again."""


# ---------------------------------------------------------------- the display law, in numpy


def _smoothstep(e0: float, e1: float, x: F) -> F:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return np.asarray(t * t * (3.0 - 2.0 * t))


def _law(x: F) -> F:
    return np.asarray(np.clip(np.arcsinh((x - LO) / SOFT) / math.asinh((HI - LO) / SOFT), 0.0, 1.0))


def _sn_law(x: F) -> F:
    x = np.maximum(x - 2.0, 0.0)  # the first 2 sigma are noise: dark
    return np.asarray(np.clip(np.arcsinh(x / BN_KNEE) / math.asinh(BN_FULL / BN_KNEE), 0.0, 1.0))


def plane_levels(rgb: F) -> tuple[list[float], list[float]]:
    """Background (median) and noise (1.4826 × MAD) of each channel on its own, on every third pixel like the lab.
    Noise 0 = not measured."""
    bg: list[float] = []
    sg: list[float] = []
    for c in range(rgb.shape[0]):
        v = rgb[c].ravel()[::3]
        v = v[np.isfinite(v)]
        if v.size < 100:
            bg.append(0.0)
            sg.append(0.0)
            continue
        m = float(np.median(v))
        bg.append(m)
        sg.append(max(1e-6, 1.4826 * float(np.median(np.abs(v - m)))))
    return bg, sg


def star_balance(rgb: F, sat: NDArray[np.uint8]) -> tuple[float, float] | None:
    """(kR, kB) on the peaks of the brightest unsaturated stars of the pass (web/src/viewer/balance.ts), or None with too few stars."""
    r, g, b = rgb[0], rgb[1], rgb[2]
    samp = g.ravel()[::7]
    samp = samp[np.isfinite(samp)]
    if samp.size < 100:
        return None
    bg_g = float(np.median(samp))
    rs, bs = r.ravel()[::7], b.ravel()[::7]
    bg_r = float(np.median(rs[np.isfinite(rs)])) if np.isfinite(rs).any() else 0.0
    bg_b = float(np.median(bs[np.isfinite(bs)])) if np.isfinite(bs).any() else 0.0
    noise = 1.4826 * float(np.median(np.abs(samp - bg_g)))
    bad = maximum_filter(((sat > 0) | ~np.isfinite(r) | ~np.isfinite(g) | ~np.isfinite(b)).astype(np.uint8), size=9, mode="constant") > 0
    gg = np.where(np.isfinite(g), g, -np.inf)
    top = (gg >= maximum_filter(gg, size=7, mode="constant", cval=-np.inf)) & ~bad & (gg - bg_g > 10 * noise)
    top[:3, :] = top[-3:, :] = False
    top[:, :3] = top[:, -3:] = False
    ys, xs = np.nonzero(top)
    if ys.size == 0:
        return None
    order = np.argsort(-gg[ys, xs])[:30]
    ys, xs = ys[order], xs[order]
    dr, dg, db = r[ys, xs] - bg_r, g[ys, xs] - bg_g, b[ys, xs] - bg_b
    ok = (dr > 0) & (dg > 0) & (db > 0)
    if int(ok.sum()) < 6:
        return None
    return float(np.median(dg[ok] / dr[ok])), float(np.median(dg[ok] / db[ok]))


@dataclass(frozen=True)
class PassLook:
    """What the lab measures once per pass on the WHOLE field: the white balance, and the background and noise of each channel."""

    kr: float
    kb: float
    bg: tuple[float, float, float]
    sg: tuple[float, float, float]


def pass_look(rgb: F, sat: NDArray[np.uint8], meta_wb: dict[str, Any] | None) -> PassLook:
    wb = star_balance(rgb, sat)
    kr, kb = wb if wb is not None else (float((meta_wb or {}).get("kr", 1.0)), float((meta_wb or {}).get("kb", 1.0)))
    bg, sg = plane_levels(rgb)
    return PassLook(kr, kb, (bg[0], bg[1], bg[2]), (sg[0], sg[1], sg[2]))


def colour_of(rgb: F, look: PassLook) -> tuple[F, NDArray[np.bool_]]:
    """The colour picture of the lab for one pass: (h, w, 3) in 0..1 and where it has data."""
    r, g, b = (np.asarray(rgb[i], np.float64) for i in range(3))
    ok = np.isfinite(r) & np.isfinite(g) & np.isfinite(b)
    r, g, b = (np.where(ok, v, 0.0) for v in (r, g, b))
    fixed = np.stack([_law(r * look.kr), _law(g), _law(b * look.kb)], axis=-1)
    if look.sg[1] > 0.0:
        ref = (max(look.sg[0] * look.kr, 1e-9) * max(look.sg[1], 1e-9) * max(look.sg[2] * look.kb, 1e-9)) ** (1.0 / 3.0)
        s = np.stack(
            [_sn_law((r - look.bg[0]) * look.kr / ref), _sn_law((g - look.bg[1]) / ref), _sn_law((b - look.bg[2]) * look.kb / ref)],
            axis=-1,
        )
        hot = _smoothstep(150.0, 1500.0, (g - look.bg[1]) / max(look.sg[1], 1e-9))[..., None]
        s = s + (fixed - s) * hot
    else:
        s = fixed
    lum = s.mean(axis=-1, keepdims=True)
    fade = 1.0 - _smoothstep(0.55, 0.9, lum)
    faint = _smoothstep(0.04, 0.16, lum)
    out = np.clip(lum + (s - lum) * (1.0 + (BOOST - 1.0) * fade * faint), 0.0, 1.0)
    return out, ok


def _shift(a: F, dx: int, dy: int) -> F:
    """a moved by (dx, dy) with the edge repeated (a texture read clamped to the edge)."""
    h, w = a.shape
    ys = np.clip(np.arange(h) + dy, 0, h - 1)
    xs = np.clip(np.arange(w) + dx, 0, w - 1)
    return np.asarray(a[np.ix_(ys, xs)])


def pass_picture(rgb: F, sat: NDArray[np.uint8], look: PassLook, step_arcsec: float) -> F:
    """One pass as the lab draws it with "Saturated cores white" on: the colour picture, and a smooth white disc on every saturated core
    (holes the overflow leaves round a core belong to it); no data anywhere else is black."""
    col, ok = colour_of(rgb, look)
    share = sat.astype(np.float64) / 255.0
    hole = (~ok).astype(np.float64)
    dp = NATIVE_PIXEL_ARCSEC / step_arcsec  # one detector pixel in grid pixels
    a = np.zeros_like(share)
    b = np.zeros_like(share)
    w = 0.0
    for j in range(-2, 3):
        for i in range(-2, 3):
            ox, oy = i * 0.5, j * 0.5
            wt = math.exp(-(ox * ox + oy * oy) * 1.1)
            dx, dy = round(ox * dp), round(oy * dp)
            s0 = _shift(share, dx, dy)
            a += wt * np.maximum(s0, _shift(hole, dx, dy))
            b += wt * s0
            w += wt
    sm = np.where(b / w < 0.04, 0.0, a / w)
    core = _smoothstep(0.22, 0.58, sm)[..., None]
    base = np.where(ok[..., None], col, np.where(core > 0.2, 1.0, np.asarray(NO_DATA)))
    return np.asarray(base + (1.0 - base) * core)


# ---------------------------------------------------------------- where the object is in each pass


def _mjd_date(mjd: float) -> datetime:
    return datetime(1858, 11, 17, tzinfo=UTC) + timedelta(days=float(mjd))


def date_label(mjd0: float, mjd1: float) -> str:
    """The dates of a pass (it is a stack of many days): "12 Aug - 27 Sep 2025". Plain ASCII: the label is drawn with the small
    font Pillow carries."""
    a, b = _mjd_date(mjd0), _mjd_date(mjd1)
    if a.date() == b.date():
        return f"{a.day} {a:%b %Y}"
    if a.year == b.year:
        return f"{a.day} {a:%b} - {b.day} {b:%b %Y}"
    return f"{a.day} {a:%b %Y} - {b.day} {b:%b %Y}"


def motion_of(find: dict[str, Any]) -> tuple[str, float | None] | None:
    """The measured motion of a find for the picture: ('8.84"/yr', position angle) from the line through its measured places; a find
    that only carries a rate gets the text without an arrow; a find that stands still gets nothing."""
    tr = track_of(find)
    if len(tr) >= 2 and tr[-1][0] - tr[0][0] >= 1.0:
        t0, t1 = tr[0][0], tr[-1][0]
        (ra0, dec0), (ra1, dec1) = place_at(find, t0), place_at(find, t1)
        de = ((ra1 - ra0 + 540.0) % 360.0 - 180.0) * math.cos(math.radians(0.5 * (dec0 + dec1))) * 3600.0
        dn = (dec1 - dec0) * 3600.0
        rate = math.hypot(de, dn) / ((t1 - t0) / 365.25)
        if rate > 0:
            return f'{rate:.2f}"/yr', math.degrees(math.atan2(de, dn)) % 360.0
    if find.get("kind") == "film" and find.get("rate"):
        pa = ((find.get("data") or {}).get("track") or {}).get("pa")
        return f'{float(find["rate"]):.1f}"/h', float(pa) if pa is not None else None
    if find.get("kind") in ("mover", "browndwarf") and find.get("rate"):
        return f'{float(find["rate"]):.2f}"/yr', None
    return None


def track_of(find: dict[str, Any]) -> list[tuple[float, float, float]]:
    """(mjd, ra, dec) of the object in the passes it was measured in; empty for a find that stands still or has no measured path.
    A Planet X peak has none on purpose: its other passes lie a degree away (a body at tens of au), outside any clip."""
    d = find.get("data") or {}
    t: dict[str, Any] = {}
    if find.get("kind") == "mover":
        t = d.get("track") or {}
    elif find.get("kind") == "browndwarf":
        t = (d.get("source") or {}).get("track") or {}
    out = []
    for m, ra, dec in zip(t.get("mjd") or [], t.get("ra") or [], t.get("dec") or [], strict=False):
        if m is not None and ra is not None and dec is not None:
            out.append((float(m), float(ra), float(dec)))
    return out


def place_at(find: dict[str, Any], mjd: float) -> tuple[float, float]:
    """Where the object stands at `mjd`: the straight line through its measured places, or its one catalogued place."""
    tr = track_of(find)
    if len(tr) < 2:
        return float(find["ra"]), float(find["dec"])
    t = np.array([p[0] for p in tr])
    ra = np.unwrap(np.radians([p[1] for p in tr]))
    dec = np.array([p[2] for p in tr])
    if float(np.ptp(t)) < 1.0:
        return float(np.degrees(ra.mean()) % 360.0), float(dec.mean())
    pr, pd = np.polyfit(t - t.mean(), ra, 1), np.polyfit(t - t.mean(), dec, 1)
    return float(np.degrees(np.polyval(pr, mjd - t.mean())) % 360.0), float(np.polyval(pd, mjd - t.mean()))


# ---------------------------------------------------------------- the frames


@dataclass
class Source:
    """A finished field a clip can be cut from."""

    store: Store
    key: str
    meta: dict[str, Any]
    own: bool  # built for clips (in the clips' store)

    @property
    def grid(self) -> Grid:
        g = self.meta["grid"]
        return Grid(float(g["ra0"]), float(g["dec0"]), float(g["step_arcsec"]), int(g["n"]))


_FONT: dict[int, Any] = {}


def _font(px: int) -> Any:
    if px not in _FONT:
        try:
            _FONT[px] = ImageFont.load_default(size=px)
        except (TypeError, OSError):  # a Pillow without FreeType: its bitmap font
            _FONT[px] = ImageFont.load_default()
    return _FONT[px]


def _text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, px: int, anchor: str = "la") -> None:
    font = _font(px)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
        draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=(0, 0, 0, 200), anchor=anchor)
    draw.text(xy, text, font=font, fill=(255, 255, 255, 235), anchor=anchor)


def draw_frame(
    pic: F,
    ring: tuple[float, float] | None,
    ring_px: float,
    label: str,
    corner: str,
    size: int = SIZE,
    motion: tuple[str, float | None] | None = None,
    labels: bool = True,
) -> Image.Image:
    """One frame: the window enlarged to `size`, the ring (if the object is in this pass), the dates of the pass and the width of
    the window. `motion` = (the measured rate as text, the direction as a position angle in degrees east of north or None): the rate is
    written above the dates and a short arrow leaves the ring the way the object goes (north is up, east is left)."""
    h = pic.shape[0]
    img = Image.fromarray(np.clip(np.round(pic * 255.0), 0, 255).astype(np.uint8), "RGB").resize((size, size), Image.Resampling.BICUBIC)
    k = 3  # the overlay is drawn three times larger and reduced: smooth ring, crisp text
    over = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    if ring is not None:
        s = size * k / h
        cx, cy, rr = (ring[0] + 0.5) * s, (ring[1] + 0.5) * s, ring_px * s
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(0, 0, 0, 140), width=2 * k + 1)
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(127, 240, 228, 255), width=k)
        if motion is not None and motion[1] is not None:
            a = math.radians(motion[1])
            ux, uy = -math.sin(a), -math.cos(a)
            x1, y1 = cx + ux * rr * 1.2, cy + uy * rr * 1.2
            x2, y2 = cx + ux * (rr * 1.2 + 0.085 * size * k), cy + uy * (rr * 1.2 + 0.085 * size * k)
            hd = 0.03 * size * k
            head = [
                (x2, y2),
                (x2 - ux * hd - uy * hd * 0.55, y2 - uy * hd + ux * hd * 0.55),
                (x2 - ux * hd + uy * hd * 0.55, y2 - uy * hd - ux * hd * 0.55),
            ]
            d.line((x1, y1, x2, y2), fill=(0, 0, 0, 150), width=2 * k + 1)
            d.line((x1, y1, x2 - ux * hd * 0.6, y2 - uy * hd * 0.6), fill=(127, 240, 228, 255), width=k)
            d.polygon(head, fill=(127, 240, 228, 255))
    over = over.resize((size, size), Image.Resampling.LANCZOS)
    img = Image.alpha_composite(img.convert("RGBA"), over)
    if not labels:  # a small picture (a row of a list): the text would not be readable
        return img.convert("RGB")
    d2 = ImageDraw.Draw(img)
    _text(d2, (8, size - 8), label, 13, "ls")
    if motion is not None and size >= 200:
        _text(d2, (8, size - 26), motion[0], 14, "ls")
    _text(d2, (size - 8, size - 8), corner, 12, "rs")
    return img.convert("RGB")


def _window(a: NDArray[Any], x0: int, y0: int, w: int, fill: float) -> NDArray[Any]:
    """The w × w window of the last two axes of `a` whose top-left is (x0, y0); beyond the field: `fill`."""
    out = np.full((*a.shape[:-2], w, w), fill, a.dtype)
    n_y, n_x = a.shape[-2:]
    ys, ye, xs, xe = max(0, y0), min(n_y, y0 + w), max(0, x0), min(n_x, x0 + w)
    if ye > ys and xe > xs:
        out[..., ys - y0 : ye - y0, xs - x0 : xe - x0] = a[..., ys:ye, xs:xe]
    return out


_LOOKS: OrderedDict[tuple[str, int], PassLook] = OrderedDict()


def render(
    find: dict[str, Any],
    src: Source,
    window_arcmin: float = WINDOW_ARCMIN,
    size: int = SIZE,
    with_ring: bool = True,
    labels: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    """The clip of `find` cut from the field `src`: (animated WebP, what it shows). Raises ClipUnavailable when the field has no pass
    there."""
    t0 = time.time()
    grid = src.grid
    passes = sorted(src.meta.get("passes") or [], key=lambda p: p["index"])
    if not passes:
        raise ClipUnavailable("the field has no pass yet")
    mid = float(np.mean([p["mjd_mid"] for p in passes]))
    cra, cdec = place_at(find, mid)
    cx, cy = (float(v) for v in grid.sky_to_grid(cra, cdec))
    w = round(window_arcmin * 60.0 / grid.step_arcsec)
    margin = 2 * math.ceil(NATIVE_PIXEL_ARCSEC / grid.step_arcsec)  # room for the blur of the saturated cores
    x0, y0 = round(cx - w / 2.0), round(cy - w / 2.0)
    frames: list[Image.Image] = []
    shown: list[dict[str, Any]] = []
    motion = motion_of(find) if with_ring else None
    for p in passes:
        raw = src.store.read_part(FIELD, src.key, f"picture_p{p['index']}.sxp")
        if raw is None:
            continue
        _, arr = decode(raw)
        rgb = arr["rgb"].astype(np.float32)
        sat = arr["sat"] if "sat" in arr else np.zeros(rgb.shape[1:], np.uint8)
        memo = (src.key, int(p["index"]))
        look = _LOOKS.get(memo)
        if look is None:
            look = _LOOKS[memo] = pass_look(rgb, sat, p.get("white_balance"))
            while len(_LOOKS) > 256:
                _LOOKS.popitem(last=False)
        big = w + 2 * margin
        c_rgb = _window(rgb, x0 - margin, y0 - margin, big, np.nan)
        c_sat = _window(sat, x0 - margin, y0 - margin, big, 0)
        if not np.isfinite(c_rgb[1][margin:-margin, margin:-margin]).any() and not c_sat.any():
            continue  # this pass did not cover the place
        pic = pass_picture(c_rgb, c_sat, look, grid.step_arcsec)[margin:-margin, margin:-margin]
        ring: tuple[float, float] | None = None
        ra, dec = place_at(find, float(p["mjd_mid"]))
        rx, ry = (float(v) for v in grid.sky_to_grid(ra, dec))
        if with_ring and -0.5 <= rx - x0 <= w - 0.5 and -0.5 <= ry - y0 <= w - 0.5:
            ring = (rx - x0, ry - y0)
        label = date_label(float(p["mjd0"]), float(p["mjd1"]))
        mot = motion if ring else None
        frames.append(draw_frame(pic, ring, RING_ARCSEC / grid.step_arcsec, label, f"{window_arcmin:g} arcmin", size, mot, labels))
        shown.append({"pass": int(p["index"]), "mjd0": p["mjd0"], "mjd1": p["mjd1"], "label": label, "ra": ra, "dec": dec})
    if not frames:
        raise ClipUnavailable("no pass of the field covers this place")
    buf = io.BytesIO()
    hold = [FRAME_MS] * (len(frames) - 1) + [LAST_HOLD_MS if len(frames) > 1 else FRAME_MS]
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=hold, loop=0, quality=QUALITY, method=4)
    data = buf.getvalue()
    info = {
        "version": VERSION,
        "id": find["id"],
        "kind": find.get("kind"),
        "field": src.key,
        "field_fov": (src.meta.get("params") or {}).get("fov"),
        "own_field": src.own,
        "step_arcsec": grid.step_arcsec,
        "centre": {"ra": cra, "dec": cdec},
        "window_arcmin": window_arcmin,
        "size": size,
        "frame_ms": FRAME_MS,
        "frames": shown,
        "bytes": len(data),
        "render_s": round(time.time() - t0, 3),
        "made": time.time(),
    }
    return data, info


# ---------------------------------------------------------------- a body that moves inside one pass: frames of the visits

FILM = "film"
FILM_FRAME_MS = 550
FILM_MAX_FRAMES = 14
FILM_MIN_WINDOW_ARCMIN = 4.0
FILM_MARGIN_ARCMIN = 1.5


def film_picture(sky: NDArray[np.uint8]) -> F:
    """A frame of the visits product as the lab's Days scale draws it: grey in the lab's display law (the product keeps 1 + 254 × the
    law; 0 = no data). The visits carry no saturation layer, so a hole next to full-scale light is a saturated core and is white;
    any other hole (no exposure there in this visit) is black."""
    v = np.where(sky > 0, (sky.astype(np.float64) - 1.0) / 254.0, 0.0)
    hole = sky == 0
    core = hole & (maximum_filter(sky, size=7, mode="nearest") >= 235)
    v[core] = 1.0
    return np.repeat(v[..., None], 3, axis=-1)


def render_film(find: dict[str, Any], store: Store, key: str, meta: dict[str, Any], size: int = SIZE) -> tuple[bytes, dict[str, Any]]:
    """The clip of a time-lapse track: the visits of its pass in ONE detector (so the wavelength never changes between frames), the same
    window in every frame, the ring where the track's straight line puts the body at the time of each visit."""
    t0 = time.time()
    g = meta["grid"]
    grid = Grid(float(g["ra0"]), float(g["dec0"]), float(g["step_arcsec"]), int(g["n"]))
    frames_meta = meta.get("frames") or []
    pts = [p for p in ((find.get("data") or {}).get("track") or {}).get("pts") or [] if 0 <= int(p.get("f", -1)) < len(frames_meta)]
    if len(pts) < 2:
        raise ClipUnavailable("the track has no measured places in the visits")
    raw = store.read_part(FILM, key, "film.sxp")
    if raw is None:
        raise ClipUnavailable("the visits of this pass are not built here")
    _, arr = decode(raw)
    sky = arr["sky"]
    t = np.array([float(frames_meta[int(p["f"])]["mjd"]) for p in pts])
    xs, ys = np.array([float(p["x"]) for p in pts]), np.array([float(p["y"]) for p in pts])
    tm = t.mean()
    if float(np.ptp(t)) > 0:
        px, py = np.polyfit(t - tm, xs, 1), np.polyfit(t - tm, ys, 1)
    else:
        px, py = np.array([0.0, xs.mean()]), np.array([0.0, ys.mean()])
    dets = [int(frames_meta[int(p["f"])]["det"]) for p in pts]
    det = max(set(dets), key=dets.count)  # the detector that saw the body most often
    f0, f1 = min(int(p["f"]) for p in pts), max(int(p["f"]) for p in pts)
    chosen = [i for i in range(f0, f1 + 1) if int(frames_meta[i]["det"]) == det]
    if len(chosen) > FILM_MAX_FRAMES:
        chosen = [chosen[round(k * (len(chosen) - 1) / (FILM_MAX_FRAMES - 1))] for k in range(FILM_MAX_FRAMES)]
    if not chosen:
        raise ClipUnavailable("no visit of the track's detector in this pass")
    where = [
        (float(np.polyval(px, float(frames_meta[i]["mjd"]) - tm)), float(np.polyval(py, float(frames_meta[i]["mjd"]) - tm))) for i in chosen
    ]
    wx = [w[0] for w in where]
    wy = [w[1] for w in where]
    span = max(max(wx) - min(wx), max(wy) - min(wy)) * grid.step_arcsec / 60.0
    window = min(max(FILM_MIN_WINDOW_ARCMIN, span + 2 * FILM_MARGIN_ARCMIN), grid.n * grid.step_arcsec / 60.0)
    w = max(8, round(window * 60.0 / grid.step_arcsec))
    x0 = round((max(wx) + min(wx)) / 2.0 - w / 2.0)
    y0 = round((max(wy) + min(wy)) / 2.0 - w / 2.0)
    x0, y0 = min(max(x0, 0), max(0, grid.n - w)), min(max(y0, 0), max(0, grid.n - w))
    ring_px = max(RING_ARCSEC / grid.step_arcsec, 0.035 * w)  # in a wide window the ring keeps a visible size
    frames: list[Image.Image] = []
    shown: list[dict[str, Any]] = []
    for i, (rx, ry) in zip(chosen, where, strict=True):
        pic = film_picture(_window(sky[i], x0, y0, w, 0))
        mjd = float(frames_meta[i]["mjd"])
        label = f"{_mjd_date(mjd).day} {_mjd_date(mjd):%b %Y %H:%M} UT"
        ring = (rx - x0, ry - y0) if -0.5 <= rx - x0 <= w - 0.5 and -0.5 <= ry - y0 <= w - 0.5 else None
        frames.append(draw_frame(pic, ring, ring_px, label, f"{window:.0f} arcmin", size, motion_of(find) if ring else None))
        ra, dec = (float(v) for v in grid.grid_to_sky(rx, ry))
        shown.append({"frame": i, "visit": frames_meta[i].get("visit"), "det": det, "mjd": mjd, "label": label, "ra": ra, "dec": dec})
    buf = io.BytesIO()
    hold = [FILM_FRAME_MS] * (len(frames) - 1) + [LAST_HOLD_MS]
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=hold, loop=0, quality=QUALITY, method=4)
    data = buf.getvalue()
    info = {
        "version": VERSION,
        "id": find["id"],
        "kind": find.get("kind"),
        "visits_product": key,
        "detector": f"D{det}",
        "step_arcsec": grid.step_arcsec,
        "window_arcmin": round(window, 2),
        "size": size,
        "frame_ms": FILM_FRAME_MS,
        "frames": shown,
        "bytes": len(data),
        "render_s": round(time.time() - t0, 3),
        "made": time.time(),
    }
    return data, info


# ---------------------------------------------------------------- the clips of the service


def clips_dir(store_dir: Path) -> Path:
    return Path(os.environ.get("SKY_CLIPS") or (store_dir / "clips")).resolve()


BuildFn = Callable[[Store, dict[str, Any]], Awaitable[str | None]]


class Clips:
    """Finds the frames of a finding, renders its clip once and keeps it. `build` (given by the service) builds a 5′ field into `self.own`
    and returns its key, or None when no exposure covers the place; without it only finished fields are used."""

    def __init__(self, store: Store, root: Path, build: BuildFn | None = None) -> None:
        self.store, self.root, self.build = store, root, build
        self.own = Store(root)
        self.gate = asyncio.Semaphore(2)  # renders at once (each is a thread busy for a fraction of a second)
        self.building: dict[str, asyncio.Task[str | None]] = {}
        self.failed: dict[str, str] = {}
        self._fields: tuple[float, list[Source]] = (0.0, [])
        self._visits: tuple[float, list[tuple[str, dict[str, Any], dict[str, Any]]]] = (0.0, [])
        self.pre: dict[str, Any] = {"running": False, "rounds": 0, "made": 0, "left": None, "now": None}
        self._pre_task: asyncio.Task[None] | None = None

    # -- files

    def path(self, find_id: str) -> Path:
        safe = find_id.replace(":", "_").replace("/", "_")
        return self.root / VERSION / safe[-2:] / f"{safe}.webp"

    def ready(self, find_id: str) -> bool:
        return self.path(find_id).is_file()

    def info(self, find_id: str) -> dict[str, Any] | None:
        p = self.path(find_id).with_suffix(".json")
        try:
            return dict(json.loads(p.read_text()))
        except (OSError, ValueError):
            return None

    def state(self, find_id: str) -> str:
        """`ready`, `building` (its field is being built), `none` (it cannot be made: see `failed`) or `waiting` (not rendered yet)."""
        if self.ready(find_id):
            return "ready"
        if find_id in self.building:
            return "building"
        return "none" if find_id in self.failed else "waiting"

    # -- the fields a clip can be cut from

    def sources(self) -> list[Source]:
        t, have = self._fields
        if time.time() - t < 20.0:
            return have
        out: list[Source] = []
        for st, own in ((self.store, False), (self.own, True)):
            for row in st.listing((FIELD,)):
                if not row["complete"]:
                    continue
                meta = st.read_meta(FIELD, row["key"])
                if meta and meta.get("passes") and meta.get("grid"):
                    out.append(Source(st, row["key"], meta, own))
        self._fields = (time.time(), out)
        return out

    def source_for(self, find: dict[str, Any], window_arcmin: float = WINDOW_ARCMIN) -> Source | None:
        """The best finished field that holds the whole window round the find: the finest grid, then the most passes, then the smallest."""
        ra, dec = float(find["ra"]), float(find["dec"])
        best: tuple[tuple[float, int, float], Source] | None = None
        for s in self.sources():
            g = s.grid
            x, y = (float(v) for v in g.sky_to_grid(ra, dec))
            half = window_arcmin * 30.0 / g.step_arcsec
            if not (half - 1 <= x <= g.n - half and half - 1 <= y <= g.n - half):
                continue
            rank = (g.step_arcsec, -len(s.meta["passes"]), float(g.n))
            if best is None or rank < best[0]:
                best = (rank, s)
        return best[1] if best else None

    def visits_for(self, find: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """The visits product a time-lapse track was found in: the same pass (its date), the same field size, the track inside it."""
        d = find.get("data") or {}
        want = d.get("film")
        first = (d.get("visits") or [{}])[0]
        ra, dec = float(first.get("ra", find["ra"])), float(first.get("dec", find["dec"]))
        t, have = self._visits
        if time.time() - t > 30.0:
            have = []
            for row in self.store.listing((FILM,)):
                meta = self.store.read_meta(FILM, row["key"]) if row["complete"] else None
                if meta and meta.get("frames"):
                    have.append((row["key"], row["params"], meta))
            self._visits = (time.time(), have)
        for key, p, meta in have:
            if want is None or abs(float(p.get("mjd", -1e9)) - float(want)) > 0.06:
                continue
            if find.get("fov") is not None and abs(float(p.get("fov", 0.0)) - float(find["fov"])) > 0.01:
                continue
            g = meta["grid"]
            x, y = Grid(float(g["ra0"]), float(g["dec0"]), float(g["step_arcsec"]), int(g["n"])).sky_to_grid(ra, dec)
            if -1 <= float(x) <= g["n"] and -1 <= float(y) <= g["n"]:
                return key, meta
        return None

    def rank(self, find: dict[str, Any]) -> int:
        """For the order of the page: 0 the clip is ready, 1 it can be made, 2 it cannot (nothing covers the place; a track whose visits
        are not kept)."""
        st = self.state(str(find["id"]))
        if st == "ready":
            return 0
        if st == "none" or (find.get("kind") == "film" and self.visits_for(find) is None):
            return 2
        return 1

    def prerender(self, finds: Callable[[], Awaitable[list[dict[str, Any]]]], build_kinds: tuple[str, ...] = MOVING) -> None:
        """Starts (once) the background queue that makes every clip before anybody asks: the finds in the order of the page, one at a
        time. A 5′ field is built in the background only for the kinds in `build_kinds` (the few that move between passes); a find
        that stands still is rendered when a finished field holds it, and otherwise waits until somebody opens its card."""
        if self._pre_task is not None and not self._pre_task.done():
            return

        async def loop() -> None:
            while True:
                todo = [f for f in await finds() if not self.ready(str(f["id"])) and str(f["id"]) not in self.failed]
                self.pre.update({"running": True, "left": len(todo)})
                for f in todo:
                    self.pre["now"] = f["id"]
                    if f.get("kind") != "film" and f.get("kind") not in build_kinds and self.wants_own(f, self.source_for(f)):
                        continue
                    try:
                        await self.get(f, wait=True)
                        self.pre["made"] += 1
                    except (ClipUnavailable, ClipPending):
                        pass
                    except Exception as e:  # one clip that fails does not stop the queue
                        self.failed[str(f["id"])] = f"the clip could not be made: {e}"[:200]
                    self.pre["left"] = max(0, int(self.pre["left"] or 0) - 1)
                    await asyncio.sleep(0)
                self.pre.update({"running": False, "now": None, "rounds": self.pre["rounds"] + 1})
                await asyncio.sleep(300.0)

        self._pre_task = asyncio.create_task(loop())

    def wants_own(self, find: dict[str, Any], src: Source | None) -> bool:
        """A place no finished field holds, and a slow mover that only the coarse grid of a 60′ field holds, get a 5′ field of their own."""
        if src is None:
            return True
        return find.get("kind") in MOVING and src.grid.step_arcsec > STEP_FINE_ARCSEC * 1.01

    # -- making

    async def get(self, find: dict[str, Any], wait: bool = False) -> Path:
        """The clip's file, rendered now if its frames are at hand. Raises ClipPending while its field is being built (unless `wait`), and
        ClipUnavailable when nothing covers the place."""
        fid = str(find["id"])
        p = self.path(fid)
        if p.is_file():
            return p
        if find.get("kind") == "film":
            return await self._get_film(find, p)
        src = self.source_for(find)
        if self.wants_own(find, src) and self.build is not None and fid not in self.failed:
            task = self.building.get(fid)
            if task is None:
                if len(self.building) >= MAX_BUILDS:
                    raise ClipPending("other clips are being built first")
                task = self.building[fid] = asyncio.create_task(self._build(find))
            if not wait and not task.done():
                await asyncio.wait({task}, timeout=0.05)  # a build that answers at once is not asked for twice
            if not wait and not task.done():
                raise ClipPending("the field of this clip is being built")
            try:
                key = await task
            finally:
                self.building.pop(fid, None)
            self._fields = (0.0, [])
            if key is not None:
                meta = self.own.read_meta(FIELD, key)
                if meta and meta.get("passes"):
                    src = Source(self.own, key, meta, True)
        if src is None:
            self.failed.setdefault(fid, "no SPHEREx field covers this place yet")
            raise ClipUnavailable(self.failed[fid])
        async with self.gate:
            if not p.is_file():
                try:
                    data, info = await asyncio.to_thread(render, find, src)
                except ClipUnavailable as e:
                    self.failed[fid] = str(e)
                    raise
                atomic_write_bytes(p, data)
                atomic_write_bytes(p.with_suffix(".json"), json.dumps(info, separators=(",", ":")).encode())
        return p

    async def place(self, ra: float, dec: float, size: int = 170, ring: bool = False, labels: bool = True) -> Path:
        """The same time-lapse centred on a place (a mark, a row of the cabinet), cut ONLY from a field that is already finished: it
        never starts a field job. The place is rounded to 1e-4°. Raises ClipUnavailable when no finished field holds it."""
        ra, dec = round(ra % 360.0, 4), round(dec, 4)
        labels = labels and size >= LABELS_MIN_SIZE  # the dates do not fit a small picture: it carries no text
        fid = f"place_{ra:.4f}_{dec:+.4f}_s{size}{'_r' if ring else ''}{'' if labels else '_t0'}"
        p = self.path(fid)
        if p.is_file():
            return p
        find = {"id": fid, "kind": "place", "ra": ra, "dec": dec, "data": {}}
        src = self.source_for(find)
        if src is None:
            raise ClipUnavailable("no SPHEREx field covers this place yet")
        async with self.gate:
            if not p.is_file():
                data, info = await asyncio.to_thread(render, find, src, WINDOW_ARCMIN, size, ring, labels)
                atomic_write_bytes(p, data)
                atomic_write_bytes(p.with_suffix(".json"), json.dumps(info, separators=(",", ":")).encode())
        return p

    async def _get_film(self, find: dict[str, Any], p: Path) -> Path:
        """A body that moves inside one pass is not in the stacked passes (a stack keeps what stands still): its clip is made of the
        visits of that pass, when the service has them."""
        fid = str(find["id"])
        hit = await asyncio.to_thread(self.visits_for, find)
        if hit is None:
            self.failed[fid] = "the visits of its pass are not kept here; open it in the lab"
            raise ClipUnavailable(self.failed[fid])
        async with self.gate:
            if not p.is_file():
                try:
                    data, info = await asyncio.to_thread(render_film, find, self.store, hit[0], hit[1])
                except ClipUnavailable as e:
                    self.failed[fid] = str(e)
                    raise
                atomic_write_bytes(p, data)
                atomic_write_bytes(p.with_suffix(".json"), json.dumps(info, separators=(",", ":")).encode())
        return p

    async def _build(self, find: dict[str, Any]) -> str | None:
        assert self.build is not None
        mid = [m for m, _, _ in track_of(find)]
        ra, dec = place_at(find, float(np.mean(mid))) if mid else (float(find["ra"]), float(find["dec"]))
        try:
            return await self.build(self.own, {"ra": ra, "dec": dec, "fov": CLIP_FOV})
        except Exception as e:  # the build failed (the reader, the memory cap): said on the card, asked again after a restart
            self.failed[str(find["id"])] = f"the field could not be built: {e}"[:200]
            return None

    def summary(self, find: dict[str, Any], detail: bool = True) -> dict[str, Any]:
        """The `clip` field of a finding in the API: where the clip is and whether it is ready."""
        fid = str(find["id"])
        st = self.state(fid)
        if st == "waiting" and find.get("kind") == "film" and self.visits_for(find) is None:
            st = "none"  # known without asking: the page shows the reason and does not request the clip
            self.failed.setdefault(fid, "the visits of its pass are not kept here; open it in the lab")
        out: dict[str, Any] = {"url": f"/api/findings/{fid}/clip.webp?v={VERSION}", "state": st, "version": VERSION}
        if st == "ready" and detail:
            i = self.info(fid) or {}
            out.update({"frames": len(i.get("frames") or []), "bytes": i.get("bytes"), "window_arcmin": i.get("window_arcmin")})
        elif st == "none":
            out["reason"] = self.failed.get(fid)
        return out
