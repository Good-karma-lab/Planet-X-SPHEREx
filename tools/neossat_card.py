"""The cross-mission card of 3I/ATLAS: a frame of CSA's NEOSSat taken on the day SPHEREx saw the comet, with the comet measured.

    uv run python tools/neossat_card.py [work-dir]

1. The calibrated frame NEOS_SCI_2025348081359_cord.fits (14 Dec 2025 08:14 UT, 15 s) is read anonymously from CADC.
2. Its WCS is refitted on Gaia DR3: the header is tens of arcseconds off, so stars found in the frame are matched to Gaia stars
   (G < 15) by the offset most pairs agree on, and the reference point is moved by the median residual.
3. The comet's place at the frame's time comes from JPL Horizons (geocentric; NEOSSat flies 800 km up).
4. The comet is measured in a 5-pixel aperture at the nearest peak; the zero point comes from the matched Gaia stars (G = ZP − 2.5 log
   flux, no colour term), the noise from blank apertures.
5. A 240 × 240 pixel cutout around the comet is written to web/public/neossat/3i-2025-12-14.png with its numbers in a .json beside it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import httpx
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import gaussian_filter, maximum_filter, median_filter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from validation.png import write_png  # noqa: E402

FRAME = "NEOS_SCI_2025348081359_cord.fits"
URL = f"https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/data/pub/NEOSSAT/{FRAME}"
OUT = ROOT / "web" / "public" / "neossat"
GAIA = "https://gea.esac.esa.int/tap-server/tap/sync"
HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
APERTURE = 5.0
CUT = 120


def peaks(img: np.ndarray, nsig: float = 8.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    bg = median_filter(img, size=31)
    r = gaussian_filter(img - bg, 1.2)
    mad = 1.4826 * np.median(np.abs(r - np.median(r)))
    m = (r == maximum_filter(r, size=7)) & (r > nsig * mad)
    y, x = np.nonzero(m)
    return x.astype(float), y.astype(float), r[y, x]


def gaia(ra: float, dec: float, radius: float) -> list[dict[str, float]]:
    q = (
        "SELECT ra, dec, pmra, pmdec, phot_g_mean_mag FROM gaiadr3.gaia_source WHERE phot_g_mean_mag < 15 AND "
        f"1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius:.4f}))"
    )
    r = httpx.post(GAIA, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": q}, timeout=120)
    r.raise_for_status()
    b = r.json()
    names = [c["name"] for c in b["metadata"]]
    return [dict(zip(names, row, strict=True)) for row in b["data"]]


def comet_place(date_obs: str) -> tuple[float, float]:
    from astropy.time import Time

    jd = Time(date_obs, scale="utc").jd
    r = httpx.get(
        HORIZONS,
        params={
            "format": "json",
            "COMMAND": "'DES=C/2025 N1;CAP;'",
            "CENTER": "'500@399'",
            "MAKE_EPHEM": "YES",
            "EPHEM_TYPE": "OBSERVER",
            "TLIST": f"'{jd:.6f}'",
            "TLIST_TYPE": "JD",
            "QUANTITIES": "'1'",
            "ANG_FORMAT": "DEG",
            "CSV_FORMAT": "YES",
            "OBJ_DATA": "NO",
        },
        timeout=60,
    )
    line = r.json()["result"].split("$$SOE")[1].split("$$EOE")[0].strip().splitlines()[0]
    parts = [q.strip() for q in line.split(",")]
    return float(parts[3]), float(parts[4])


def aperture(img: np.ndarray, x: float, y: float, r: float = APERTURE) -> float:
    yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
    d = np.hypot(xx - x, yy - y)
    ring = (d > 3 * r) & (d < 5 * r)
    return float(np.sum(img[d <= r] - np.median(img[ring])))


def main() -> int:
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "store" / "neossat"
    work.mkdir(parents=True, exist_ok=True)
    path = work / FRAME
    if not path.is_file():
        r = httpx.get(URL, follow_redirects=True, timeout=180)
        r.raise_for_status()
        path.write_bytes(r.content)
    with fits.open(path) as h:
        img = h[0].data.astype(float)
        hdr = h[0].header
    w = WCS(hdr)
    xs, ys, amp = peaks(img)
    order = np.argsort(-amp)[:300]
    xs, ys = xs[order], ys[order]
    sra, sdec = w.all_pix2world(xs, ys, 0)
    c_ra, c_dec = w.all_pix2world([img.shape[1] / 2], [img.shape[0] / 2], 0)
    stars = gaia(float(c_ra[0]), float(c_dec[0]), 0.45)
    yr = 2025.95 - 2016.0
    gra = np.array([s["ra"] + (s["pmra"] or 0) * yr / 3.6e6 / math.cos(math.radians(s["dec"])) for s in stars])
    gdec = np.array([s["dec"] + (s["pmdec"] or 0) * yr / 3.6e6 for s in stars])
    gmag = np.array([s["phot_g_mean_mag"] for s in stars])
    cosd = math.cos(math.radians(float(c_dec[0])))
    # the offset most star-Gaia pairs agree on (a 2D histogram of all pair differences within 90″)
    dra = ((gra[None, :] - sra[:, None] + 540) % 360 - 180) * cosd * 3600
    ddec = (gdec[None, :] - sdec[:, None]) * 3600
    near = np.hypot(dra, ddec) < 90
    hist, xe, ye = np.histogram2d(dra[near], ddec[near], bins=60, range=[[-90, 90], [-90, 90]])
    i, j = np.unravel_index(np.argmax(hist), hist.shape)
    off0 = (0.5 * (xe[i] + xe[i + 1]), 0.5 * (ye[j] + ye[j + 1]))
    resid = np.hypot(dra - off0[0], ddec - off0[1])
    si, gi = np.nonzero(resid < 6)
    off = (float(np.median(dra[si, gi])), float(np.median(ddec[si, gi])))
    hdr["CRVAL1"] = hdr["CRVAL1"] + off[0] / 3600 / cosd
    hdr["CRVAL2"] = hdr["CRVAL2"] + off[1] / 3600
    w = WCS(hdr)
    # zero point from the matched stars
    fl = np.array([aperture(img, xs[a], ys[a]) for a in si])
    good = fl > 0
    zp = float(np.median(gmag[gi][good] + 2.5 * np.log10(fl[good])))
    ra_c, dec_c = comet_place(hdr["DATE-OBS"])
    px, py = w.all_world2pix([ra_c], [dec_c], 0)
    px, py = float(px[0]), float(py[0])
    d = np.hypot(xs - px, ys - py)
    k = int(np.argmin(d))
    found = d[k] < 6
    cx, cy = (xs[k], ys[k]) if found else (px, py)
    flux = aperture(img, cx, cy)
    # noise: the scatter of the same aperture on blank sky
    rng = np.random.default_rng(1)
    blanks = []
    while len(blanks) < 200:
        bx, by = rng.uniform(30, img.shape[1] - 30), rng.uniform(30, img.shape[0] - 30)
        if np.min(np.hypot(xs - bx, ys - by)) > 15:
            blanks.append(aperture(img, bx, by))
    noise = 1.4826 * float(np.median(np.abs(np.array(blanks) - np.median(blanks))))
    result = {
        "frame": FRAME,
        "mission": "NEOSSat (Canadian Space Agency), via CADC",
        "date_obs": hdr["DATE-OBS"],
        "exposure_s": float(hdr["EXPOSURE"]),
        "wcs_offset_arcsec": round(float(math.hypot(*off)), 1),
        "gaia_matches": len(si),
        "zero_point_g": round(zp, 2),
        "horizons_ra": round(ra_c, 5),
        "horizons_dec": round(dec_c, 5),
        "found": bool(found),
        "offset_from_prediction_arcsec": round(float(d[k]) * 3600 * math.hypot(hdr["CD1_1"], hdr["CD2_1"]), 1),
        "g_mag": round(zp - 2.5 * math.log10(flux), 2) if flux > 0 else None,
        "snr": round(flux / noise, 0) if noise > 0 else None,
    }
    # the cutout: asinh stretch, the predicted place ringed
    x0, y0 = round(cx) - CUT, round(cy) - CUT
    cut = img[max(0, y0) : y0 + 2 * CUT, max(0, x0) : x0 + 2 * CUT]
    lo, hi = np.percentile(cut, 5), np.percentile(cut, 99.8)
    v = np.arcsinh((cut - lo) / max(1.0, (hi - lo) / 30)) / np.arcsinh(30)
    g8 = (np.clip(v, 0, 1) * 255).astype(np.uint8)[::-1, ::-1]  # north up, east left (the frame's RA grows with x)
    rgb = np.stack([g8, g8, g8], -1).copy()
    ry, rx = (2 * CUT - 1) - (py - max(0, y0)), (2 * CUT - 1) - (px - max(0, x0))
    yy, xx = np.mgrid[0 : rgb.shape[0], 0 : rgb.shape[1]]
    ring = np.abs(np.hypot(xx - rx, yy - ry) - 12) < 1.0
    rgb[ring] = [255, 180, 84]
    OUT.mkdir(parents=True, exist_ok=True)
    write_png(OUT / "3i-2025-12-14.png", rgb, scale=2)
    (OUT / "3i-2025-12-14.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
