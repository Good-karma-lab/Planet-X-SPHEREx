# mypy: disable-error-code="no-untyped-call"
"""The NEOWISE archive check of the checks before submit: ten years of WISE W1 exposures (NEOWISE-R, 2014-2024) added along the
orbit of a candidate (the engine is service.checks_archive, the port of the research check that ruled out the 394 au peak).

    frames    the single-exposure W1 frames that overlap a strip of 1 x 0.3 degrees, centred where the orbit is in the middle of the
              WISE years (IRSA image search; frames of quality 0 left out)
    cutouts   the strip of every frame (about 60 KB each, about a thousand of them), fetched from IRSA and kept on disk
    epochs    median of the exposures of an epoch (gaps of 30 days cut epochs); difference = epoch - static sky, static sources masked
    orbit     the candidate's orbit (place and time, distance, own motion) carried back to every epoch; the differences are added
              along it, weighted by the epoch noise
    scale     the static sky at isolated CatWISE stars of W1 13.5-15.8 against their catalogue flux

The answer is the flux on the orbit with its error, and what a body of the candidate's flux would give (`would_be_sigma`).
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from engine import distant as edist
from engine import orbit as eorbit
from service import checks_archive as earc

VERSION = "n1"
STRIP = (1.0, 0.3)  # degrees: the cutout of every frame
IBE = "https://irsa.ipac.caltech.edu/ibe/"
TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
WISE_MID_MJD = 58300.0  # the middle of the NEOWISE years
MIN_FRAMES, MIN_EPOCHS = 20, 4
THREADS = 8  # resampling of the cutouts already on disk
FETCH_THREADS, RETRY_THREADS = 6, 2  # requests to IRSA at a time: first round, then the cutouts that failed
RULED_OUT_SIGMA = 5.0  # a body of the candidate's flux must stand at least this high in the archive for its absence to count
RULED_OUT_BELOW = 3.0  # and the measured flux must be this many errors below the candidate's flux
SEEN_SIGMA = 3.0
# the scan of the orbit's uncertainty (`orbit.scan`): 7 distances x 5 radial speeds x 57 x 57 own motions = 113 715 orbits
SCAN_D_HALF_AU, SCAN_N_D, SCAN_MU_HALF, SCAN_WORKERS = 14.0, 7, 0.02, 6
SCAN_VR = np.linspace(-2.0, 2.0, 5)  # km/s


def strip_centre(o: dict[str, Any]) -> tuple[float, float, Any, Any]:
    """Where the nominal orbit is in the middle of the WISE years, and the orbit's frame: (ra, dec, G, state)."""
    tc = edist.Tangent(o["ra"], o["dec"])
    g = {"T": tc, "Rr": eorbit.earth(o["mjd"]), "tr": float(o["mjd"])}
    x0, v = earc.state(g, (0.0, 0.0), o["d_au"], tuple(o["mu"]), 0.0)
    xm = eorbit.kepler(x0[None, None], v[:, None], np.array([[(WISE_MID_MJD - o["mjd"]) * 86400.0]]))[0, 0]
    w = xm - eorbit.earth(WISE_MID_MJD)
    return float(np.degrees(np.arctan2(w[1], w[0])) % 360.0), float(np.degrees(np.arcsin(w[2] / np.linalg.norm(w)))), g, (x0, v)


def frames_of(client: httpx.Client, ra: float, dec: float) -> list[dict[str, Any]]:
    url = f"{IBE}search/wise/neowiser/p1bm_frm?POS={ra:.5f},{dec:.5f}&SIZE={STRIP[0]:g},{STRIP[1]:g}&INTERSECT=OVERLAPS&ct=csv"
    r = client.get(url, timeout=httpx.Timeout(150, connect=10))
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        if row.get("band") != "1" or str(row.get("qual_frame", "0")).strip() in ("0", ""):
            continue
        try:
            scan, fn = str(row["scan_id"]).strip(), int(row["frame_num"])
            out.append({"path": f"{scan[-2:]}/{scan}/{fn:03d}/{scan}{fn:03d}-w1-int-1b.fits", "mjd": float(row["mjd_obs"])})
        except (KeyError, ValueError):
            continue
    return sorted(out, key=lambda x: float(x["mjd"]))


def cutout(client: httpx.Client, cache: Path, path: str, ra: float, dec: float) -> bytes:
    """The strip of one frame (kept on disk: a frame never changes)."""
    url = f"{IBE}data/wise/neowiser/p1bm_frm/{path}?center={ra:.5f},{dec:.5f}&size={STRIP[0]:g},{STRIP[1]:g}deg&gzip=false"
    fp = cache / f"wcut_{hashlib.sha1(url.encode()).hexdigest()[:20]}.fits"
    try:
        return fp.read_bytes()
    except OSError:
        pass
    last: Exception | None = None
    for k in range(3):
        try:
            r = client.get(url, timeout=httpx.Timeout(100, connect=10))
            r.raise_for_status()
            if len(r.content) > 2880:
                tmp = fp.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(fp)
                return r.content
            last = ValueError("an empty cutout")
        except httpx.HTTPError as e:
            last = e
            time.sleep(2.0 * 2**k)
    raise RuntimeError(f"WISE cutout failed: {type(last).__name__}")


def load(blob: bytes, tw: Any) -> dict[str, Any] | None:
    """One cutout -> its pixels in mJy (the median taken out) on the strip's tangent plane; None when it holds too little."""
    with fits.open(io.BytesIO(blob)) as h:
        hd = h[0].header
        im = np.asarray(h[0].data, "f8")
    ny, nx = im.shape
    ok = np.isfinite(im)
    if nx < 8 or ny < 8 or ok.sum() < 300:
        return None
    v = im[ok]
    med = float(np.median(v))
    for _ in range(3):
        sg = 1.4826 * float(np.median(np.abs(v - med)))
        v = v[np.abs(v - med) < 3 * sg]
        med = float(np.median(v))
    yy, xx = np.mgrid[0:ny, 0:nx]
    ra, dec = WCS(hd).all_pix2world(xx.ravel(), yy.ravel(), 0)
    u, w = tw.sky2uv(ra, dec)
    conv = earc.MAGZP_MJY * 10 ** (-0.4 * float(hd["MAGZP"]))
    return {"mjd": float(hd["MJD_OBS"]), "val": ((im - med) * conv).ravel(), "ok": ok.ravel(), "u": u, "v": w}


def catwise(client: httpx.Client, ra: float, dec: float) -> list[tuple[float, float, float]]:
    q = (
        "SELECT ra, dec, w1mpro_pm FROM catwise_2020 WHERE CONTAINS(POINT('ICRS', ra, dec), "
        f"BOX('ICRS', {ra:.5f}, {dec:.5f}, {STRIP[0] / math.cos(math.radians(dec)):.4f}, {STRIP[1]:.4f})) = 1 "
        "AND w1mpro_pm BETWEEN 13.5 AND 15.8"
    )
    r = client.post(TAP, data={"LANG": "ADQL", "FORMAT": "csv", "QUERY": q, "REQUEST": "doQuery"}, timeout=httpx.Timeout(120, connect=10))
    r.raise_for_status()
    return [(float(x["ra"]), float(x["dec"]), float(x["w1mpro_pm"])) for x in csv.DictReader(io.StringIO(r.text)) if x.get("w1mpro_pm")]


def orbit_check(o: dict[str, Any], cache: Path, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The archive check of one orbit `{ra, dec, mjd, d_au, mu: [u, v] (arcsec/day, u to the west, v to the north), flux_mjy}`. Blocking
    (minutes): call it in a thread. -> {status: ok | no-frames | few-epochs | no-scale, flux_mjy, err_mjy, would_be_sigma, ...}"""
    import warnings

    say = progress or (lambda _: None)
    cache.mkdir(parents=True, exist_ok=True)
    res: dict[str, Any] = {"version": VERSION, "status": "running"}
    sra, sdec, g, (x0, v0) = strip_centre(o)
    if o.get("strip"):
        sra, sdec = o["strip"]
    res["strip_centre"] = [round(sra, 5), round(sdec, 5)]
    with httpx.Client(headers={"User-Agent": "spherex-sky/0.1"}, follow_redirects=True) as client:
        say("listing the NEOWISE frames of the strip")
        frames = frames_of(client, sra, sdec)
        res["frames"] = len(frames)
        if len(frames) < MIN_FRAMES:
            return {**res, "status": "no-frames"}
        tw = edist.Tangent(sra, sdec)
        width, height = earc.WIDTH, 2 * earc.HALF_ROWS
        t = np.array([f["mjd"] for f in frames])
        edges = [0, *(np.where(np.diff(t) > 30.0)[0] + 1).tolist(), len(frames)]
        done, failed = 0, 0

        def fetch(f: dict[str, Any]) -> bool:
            try:
                cutout(client, cache, f["path"], sra, sdec)
            except Exception:
                return False
            return True

        # the cutouts first, a few at a time (IRSA answers 502 when pressed); those that failed are asked once more, two at a time
        missing = frames
        for threads in (FETCH_THREADS, RETRY_THREADS):
            got_n = 0
            with ThreadPoolExecutor(threads) as pool:
                oks = []
                for ok in pool.map(fetch, missing):
                    oks.append(ok)
                    got_n += 1
                    if got_n % 20 == 0 or got_n == len(missing):
                        have = len(frames) - len(missing) + sum(oks)
                        say(f"NEOWISE exposures fetched: {have} of {len(frames)}")
            missing = [f for f, ok in zip(missing, oks, strict=True) if not ok]
            if not missing:
                break

        def one(f: dict[str, Any]) -> tuple[float, Any] | None:
            try:
                e = load(cutout(client, cache, f["path"], sra, sdec), tw)
            except Exception:
                return None
            return None if e is None else (e["mjd"], earc.to_grid(e, width, height, earc.HALF_ROWS))

        vis: list[dict[str, Any]] = []
        with ThreadPoolExecutor(THREADS) as pool, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for a, b in itertools.pairwise(edges):
                got = []
                for r_ in pool.map(one, frames[a:b]):
                    done += 1
                    if r_ is None:
                        failed += 1
                    else:
                        got.append(r_)
                say(f"NEOWISE exposures added: {done} of {len(frames)}")
                vis.extend(earc.epoch_images(got))
        res["frames_failed"] = failed
        res["epochs"] = len(vis)
        if len(vis) < MIN_EPOCHS:
            return {**res, "status": "few-epochs"}
        say(f"differences of {len(vis)} epochs")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dv = earc.differences(vis)
            ref = np.nanmean(np.array([x["img"] for x in vis]), 0)
        say("the flux scale from CatWISE stars")
        try:
            scale, ncal = earc.calibrate(ref, tw, catwise(client, sra, sdec))
        except (httpx.HTTPError, ValueError, KeyError):
            scale, ncal = float("nan"), 0
    res["flux_scale"] = {"grid_value_per_mjy": round(float(scale), 5) if scale > 0 else None, "stars": ncal}
    res["epochs_used"] = len(dv)
    if not scale > 0 or not dv:
        return {**res, "status": "no-scale"}
    # the nominal orbit: the epoch differences added at its places, weighted by the epoch noise
    tk = np.array([x["t"] for x in dv])
    rk = np.array([x["R"] for x in dv])
    wgt = np.array([1 / x["sg"] ** 2 for x in dv])
    pn = np.round(earc.places(g, tw, width, x0, v0, tk, rk)[0]).astype(int)
    f0 = w0 = 0.0
    used = 0
    for k, x in enumerate(dv):
        px, py = int(pn[k, 0]), int(pn[k, 1])
        if 0 <= py < height and 0 <= px < width and x["good"][py, px]:
            f0 += float(x["d"][py, px]) * wgt[k]
            w0 += float(wgt[k])
            used += 1
    res["epochs_unmasked"] = used
    res["noise_mjy_per_epoch"] = [round(float(x["sg"]) / scale, 4) for x in dv]
    res["noise_mjy_all_epochs"] = round(float(1 / math.sqrt(wgt.sum()) / scale), 4)
    res["masked_share"] = round(float(np.mean([x["masked"] for x in dv])), 3)
    res["years"] = [round(2000.0 + (float(tk.min()) - 51544.5) / 365.25, 1), round(2000.0 + (float(tk.max()) - 51544.5) / 365.25, 1)]
    if used == 0:
        return {**res, "status": "masked"}
    meas, err = f0 / w0 / scale, 1 / math.sqrt(w0) / scale
    res.update(status="ok", flux_mjy=round(meas, 4), err_mjy=round(err, 4))
    flux = float(o.get("flux_mjy") or 0.0)
    if flux > 0:
        res["would_be_sigma"] = round(flux / err, 1)
        res["ruled_out"] = bool(flux / err > RULED_OUT_SIGMA and meas < flux - RULED_OUT_BELOW * err)
    res["seen"] = bool(meas > SEEN_SIGMA * err)
    if o.get("scan"):
        # the orbit's uncertainty: distance, radial speed and own motion around the nominal orbit, each orbit also shifted on the
        # sky (a box of 61 x 121 grid points): what the shifted places reach is what noise alone gives
        try:
            say("scanning the orbits around the nominal one")
            r = earc.run_scan(
                dv,
                g,
                tw,
                (0.0, 0.0),
                float(o["d_au"]),
                tuple(o["mu"]),
                d_half=SCAN_D_HALF_AU,
                n_d=SCAN_N_D,
                mu_half=SCAN_MU_HALF,
                vr=SCAN_VR,
                workers=SCAN_WORKERS,
                progress=lambda i, n: say(f"scanning the orbits around the nominal one: {i} of {n} parts"),
            )
            blocks = r["blocks"]
            res["scan"] = {
                "orbits": int(r["n_orbits"]),
                "sn_at_place": round(float(r["centre"][0]), 2),
                "hypothesis": [round(float(x), 4) for x in r["centre"][1]],  # distance (au), radial speed (km/s), own motion u, v (″/day)
                "noise_median": round(float(np.median(blocks)), 2) if len(blocks) else None,
                "noise_p97": round(float(np.percentile(blocks, 97.5)), 2) if len(blocks) else None,
                "noise_max": round(float(blocks.max()), 2) if len(blocks) else None,
                "places": len(blocks),
                "places_not_below": int((blocks >= r["centre"][0]).sum()),
            }
        except Exception as e:  # the scan is extra: its failure is said, the answer of the nominal orbit stands
            res["scan"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return res
