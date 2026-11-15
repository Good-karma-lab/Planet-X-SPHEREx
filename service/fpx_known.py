"""Is a Find Planet X lead a body JPL already knows? A lead's place at the date of each pass (the deep sum: one place per pass; the
one-pass search: its place at the reference visit) is looked up in JPL's Small-Body Identification, keeping only the slow bodies (a
body of the outer solar system moves a few arcseconds an hour), and the candidate bodies are then checked against JPL Horizons at the
pass dates: a body that stands within MATCH_ARCSEC of the lead in every pass, at the heliocentric distance the search gave, is named.

    {"state": "ok", "name": "90377 Sedna (2003 VB12)", "seps": [0.8, 0.5, 0.8], "V": 20.9, "r_au": 83.0, "dates": [...], "words": "..."}
    {"state": "none", ...}   JPL answered: no slow body stands there
    {"state": "unavailable"} JPL did not answer
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

from service.bodies import HORIZONS, SB_IDENT, _iso, _jd, _sexa_dec, _sexa_ra, horizons_command

SLOW_RATE = 15.0  # arcsec/hour: faster is a main-belt or near-Earth body, not what a search at 30-2000 au looks for
MATCH_ARCSEC = 20.0
DISTANCE_TOL = 0.06  # the heliocentric distance of the body against the search's distance (its own range, plus this)
MAX_BODIES = 6
SB_VMAG = 26.0


def _sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    return math.hypot((ra1 - ra2) * math.cos(math.radians(0.5 * (dec1 + dec2))), dec1 - dec2) * 3600.0


async def slow_bodies(client: httpx.AsyncClient, ra: float, dec: float, mjd: float, half_deg: float) -> list[dict[str, Any]]:
    """The slow bodies JPL puts in a box around a place at a date: name, V, rate (arcsec/hour), distance from the centre (arcsec)."""
    r = await client.get(
        SB_IDENT,
        params={
            "mpc-code": "500",
            "obs-time": _iso(mjd),
            "fov-ra-center": _sexa_ra(ra),
            "fov-dec-center": _sexa_dec(dec),
            "fov-ra-hwidth": f"{half_deg / max(0.05, math.cos(math.radians(dec))):.4f}",
            "fov-dec-hwidth": f"{half_deg:.4f}",
            "vmag-lim": str(SB_VMAG),
        },
    )
    r.raise_for_status()
    body = r.json()
    fields = body.get("fields_first") or body.get("fields") or []
    idx = {n: i for i, n in enumerate(fields)}
    out: list[dict[str, Any]] = []
    for row in body.get("data_first_pass") or body.get("data") or []:
        try:
            rate = math.hypot(float(row[idx['RA rate ("/h)']]), float(row[idx['Dec rate ("/h)']]))
            dist = math.hypot(float(row[idx['Dist. from center RA (")']]), float(row[idx['Dist. from center Dec (")']]))
        except (KeyError, ValueError, IndexError):
            continue
        if rate > SLOW_RATE:
            continue
        try:
            v: float | None = float(row[idx["Visual magnitude (V)"]])
        except (KeyError, ValueError, IndexError):
            v = None
        out.append({"name": str(row[0]), "V": v, "rate": rate, "dist": dist})
    out.sort(key=lambda b: b["dist"])
    return out


async def horizons_track(client: httpx.AsyncClient, name: str, mjds: list[float]) -> list[dict[str, float | None]]:
    """The body's places (geocentric), V, heliocentric and geocentric distance at the dates."""
    r = await client.get(
        HORIZONS,
        params={
            "format": "json",
            "COMMAND": f"'{horizons_command(name)}'",
            "CENTER": "'500@399'",
            "MAKE_EPHEM": "YES",
            "EPHEM_TYPE": "OBSERVER",
            "TLIST": " ".join(f"'{_jd(m):.5f}'" for m in mjds),
            "TLIST_TYPE": "JD",
            "QUANTITIES": "'1,9,19,20'",
            "ANG_FORMAT": "DEG",
            "CSV_FORMAT": "YES",
            "OBJ_DATA": "NO",
        },
    )
    r.raise_for_status()
    text = r.json().get("result", "")
    if "$$SOE" not in text:
        raise ValueError("no ephemeris")
    rows = text[text.index("$$SOE") + 5 : text.index("$$EOE")].strip().splitlines()
    out: list[dict[str, float | None]] = []
    for line in rows:
        f = [x.strip() for x in line.split(",")]

        def num(i: int, f: list[str] = f) -> float | None:
            try:
                return float(f[i])
            except (IndexError, ValueError):
                return None

        ra, dec = num(3), num(4)
        if ra is None or dec is None:
            raise ValueError("no place in the ephemeris")
        out.append({"ra": ra, "dec": dec, "V": num(5), "r": num(7), "delta": num(9)})
    return out


def dates_of(place: dict[str, Any]) -> list[float]:
    """The dates a lead's place at one pass can belong to: its own date when the result says it, else every day of the pass."""
    if place.get("mjd") is not None:
        return [float(place["mjd"])]
    lo, hi = float(place["mjd0"]), float(place["mjd1"])
    n = max(1, math.ceil(hi - lo))
    return [lo + (hi - lo) * k / n for k in range(n + 1)]


async def identify(
    client: httpx.AsyncClient, places: list[dict[str, Any]], d_lo: float, d_hi: float, limit: asyncio.Semaphore | None = None
) -> dict[str, Any]:
    """`places`: per pass {ra, dec, mjd} (its own date) or {ra, dec, mjd0, mjd1} (the pass's window); d_lo..d_hi: the distance the search
    gave (au). -> the answer described at the top of the module."""
    sem = limit or asyncio.Semaphore(2)
    first = places[0]
    ds = dates_of(first)
    mid = ds[len(ds) // 2]
    # a window means any day of it: the box must hold the body wherever it stands then (a slow body moves under 15 arcsec/hour)
    half = 60.0 / 3600.0 if first.get("mjd") is not None else min(1.5, 0.05 + SLOW_RATE * 12.0 * (ds[-1] - ds[0]) / 3600.0)
    try:
        async with sem:
            bodies = await slow_bodies(client, first["ra"], first["dec"], mid, half)
    except (httpx.HTTPError, ValueError, KeyError):
        return {"state": "unavailable"}
    best: dict[str, Any] | None = None
    for b in bodies[:MAX_BODIES]:
        mj = [m for pl in places for m in dates_of(pl)]
        try:
            async with sem:
                trk = await horizons_track(client, b["name"], mj)
        except (httpx.HTTPError, ValueError):
            continue
        seps: list[float] = []
        used: list[float] = []
        k = 0
        for pl in places:
            n = len(dates_of(pl))
            chunk = list(zip(dates_of(pl), trk[k : k + n], strict=True))
            k += n
            s_, m_ = min((_sep(pl["ra"], pl["dec"], t["ra"] or 0.0, t["dec"] or 0.0), m) for m, t in chunk)
            seps.append(round(s_, 1))
            used.append(round(m_, 2))
        if max(seps) > MATCH_ARCSEC:
            continue
        ref = trk[len(trk) // 2]
        r_au = ref.get("r")
        if r_au is not None and not (d_lo * (1 - DISTANCE_TOL) <= r_au <= d_hi * (1 + DISTANCE_TOL)):
            continue  # the place agrees but the distance does not: not this body
        cand = {
            "state": "ok",
            "name": b["name"],
            "seps": seps,
            "dates": used,
            "V": None if ref["V"] is None else round(float(ref["V"]), 1),
            "r_au": None if r_au is None else round(float(r_au), 1),
        }
        if best is None or max(seps) < max(best["seps"]):
            best = cand
    if best is None:
        return {"state": "none", "bodies_checked": min(len(bodies), MAX_BODIES), "n_slow_bodies": len(bodies)}
    short = best["name"].split("(")[0].strip()
    short = short.split(" ", 1)[1] if short.split(" ", 1)[0].isdigit() and " " in short else short
    best["words"] = (
        f"this is {short} (JPL), found blind: it stands {max(best['seps']):.1f}″ or less from JPL's place in "
        f"{'every pass' if len(places) > 1 else 'the pass'}" + (f", at {best['r_au']:.1f} au" if best["r_au"] is not None else "")
    )
    return best
