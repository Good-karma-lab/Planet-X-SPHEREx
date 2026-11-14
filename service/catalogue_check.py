"""Check of a measured spectrum against catalogue photometry (docs/plan/02-methods/2.4-spectra.md 2.4.4).

The used points of the spectrum are averaged under the filter curves of 2MASS J, H, Ks and WISE W1, W2 (photon counting: the mean of
Fν weighted by T(λ)/λ) and compared with F0 · 10^(−0.4 m) from the catalogue magnitude (zero points 1594, 1024, 666.7, 309.54 and
171.79 Jy). Catalogues come from IRSA (2MASS PSC, AllWISE) at the place moved to each survey's epoch; a band is compared only with
photometric quality A or B, and only where the spectrum covers the filter without a hole (W2 reaches beyond SPHEREx's 5 µm: shown,
not compared).
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from service.spectrum import place_at

FILTERS = Path(__file__).resolve().parent.parent / "data" / "filters"
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
BANDS = [
    # band, filter file, zero point (Jy), catalogue, column, epoch of the survey
    ("J", "2mass_j", 1594.0, "fp_psc", "j_m", 1999.5),
    ("H", "2mass_h", 1024.0, "fp_psc", "h_m", 1999.5),
    ("Ks", "2mass_ks", 666.7, "fp_psc", "k_m", 1999.5),
    ("W1", "wise_w1", 309.54, "allwise_p3as_psd", "w1mpro", 2010.5),
    ("W2", "wise_w2", 171.79, "allwise_p3as_psd", "w2mpro", 2010.5),
]
MATCH_ARCSEC = 3.0
MAX_GAP_UM = 0.15
BIN_UM = 0.04


def _curve(name: str) -> tuple[np.ndarray, np.ndarray]:
    a = np.loadtxt(FILTERS / f"{name}.dat")
    return a[:, 0] / 1e4, a[:, 1]  # µm, transmission


def synthetic(points: list[dict[str, Any]], name: str) -> tuple[float | None, str]:
    """The spectrum averaged under a filter (mJy), or None and why."""
    ok = [p for p in points if p.get("ok") and p.get("flux") is not None]
    if len(ok) < 20:
        return None, "too few points"
    lam = np.array([p["lam"] for p in ok])
    flux = np.array([p["flux"] for p in ok])
    wl, tr = _curve(name)
    inside = tr > 0.05 * tr.max()
    lo, hi = float(wl[inside].min()), float(wl[inside].max())
    if lo < lam.min() or hi > lam.max():
        return None, "beyond the spectrum's wavelengths"
    # the points binned at 0.04 µm (median; SPHEREx channels are 0.02–0.05 µm apart), then interpolated onto the filter's grid
    edges = np.arange(lo - 2 * BIN_UM, hi + 3 * BIN_UM, BIN_UM)
    centres, values = [], []
    for a, b in itertools.pairwise(edges):
        m = (lam >= a) & (lam < b)
        if m.sum() >= 1:
            centres.append(0.5 * (a + b))
            values.append(float(np.median(flux[m])))
    c = np.array(centres)
    if len(c) < 4 or np.max(np.diff(c[(c >= lo - BIN_UM) & (c <= hi + BIN_UM)]), initial=0) > MAX_GAP_UM:
        return None, "a hole in the spectrum under the filter"
    f = np.interp(wl, c, values)
    w = tr / wl
    return float(np.sum(f * w) / np.sum(w)), ""


async def _tap(client: httpx.AsyncClient, adql: str) -> list[dict[str, Any]]:
    r = await client.post(IRSA_TAP, data={"QUERY": adql, "FORMAT": "csv", "LANG": "ADQL", "REQUEST": "doQuery"})
    r.raise_for_status()
    lines = [ln for ln in r.text.strip().splitlines() if ln]
    if len(lines) < 1 or lines[0].startswith("<"):
        raise ValueError(lines[0][:200] if lines else "empty answer")
    head = [h.strip() for h in lines[0].split(",")]
    return [dict(zip(head, [v.strip() for v in ln.split(",")], strict=False)) for ln in lines[1:]]


async def check(params: dict[str, Any], points: list[dict[str, Any]], client: httpx.AsyncClient) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    cats: dict[str, dict[str, Any] | str | None] = {}
    for table, epoch, cols in (("fp_psc", 1999.5, "j_m, h_m, k_m, ph_qual"), ("allwise_p3as_psd", 2010.5, "w1mpro, w2mpro, ph_qual")):
        mjd = 51544.5 + (epoch - 2000.0) * 365.25
        ra, dec = place_at(params, mjd)
        try:
            found = await _tap(
                client,
                f"SELECT ra, dec, {cols} FROM {table} WHERE "
                f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {MATCH_ARCSEC / 3600:.6f})) = 1",
            )
        except Exception as e:  # the catalogue did not answer: said, and asked again next time
            cats[table] = f"unavailable ({type(e).__name__})"
            continue
        if not found:
            cats[table] = None
            continue
        if len(found) > 1:
            notes.append(f"{table}: {len(found)} sources within {MATCH_ARCSEC:.0f}″ (a blend): the nearest is used")
        cosd = math.cos(math.radians(dec))
        cats[table] = min(found, key=lambda s: math.hypot((float(s["ra"]) - ra) * cosd, float(s["dec"]) - dec))
    for band, fname, zp, table, col, _ in BANDS:
        syn, why = synthetic(points, fname)
        cat = cats.get(table)
        row: dict[str, Any] = {
            "band": band,
            "synth_mjy": None if syn is None else round(syn, 3),
            "cat_mjy": None,
            "mag": None,
            "ratio": None,
        }
        if isinstance(cat, str):
            row["note"] = cat
        elif cat is None:
            row["note"] = "no catalogue source within 3″"
        else:
            idx = ["J", "H", "Ks"].index(band) if table == "fp_psc" else ["W1", "W2"].index(band)
            qual = (cat.get("ph_qual") or "")[idx : idx + 1]
            try:
                mag = float(cat[col])
            except (KeyError, ValueError):
                mag = None
            if mag is not None:
                row["mag"] = round(mag, 3)
                row["cat_mjy"] = round(zp * 10 ** (-0.4 * mag) * 1000.0, 3)
            if qual not in ("A", "B"):
                row["note"] = f"catalogue quality {qual or '?'}: not compared"
            elif syn is None:
                row["note"] = why
            elif row["cat_mjy"]:
                row["ratio"] = round(syn / row["cat_mjy"], 3)
        rows.append(row)
    return {"state": "done", "rows": rows, "notes": notes}
