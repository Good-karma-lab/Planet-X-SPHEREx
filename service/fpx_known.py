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
import contextlib
import json
import logging
import math
import time
from pathlib import Path
from typing import Any

import httpx

from service.bodies import HORIZONS, SB_IDENT, _iso, _jd, _sexa_dec, _sexa_ra, horizons_command
from service.upstream import jpl

log = logging.getLogger("sky.fpx_known")

SLOW_RATE = 15.0  # arcsec/hour: faster is a main-belt or near-Earth body, not what a search at 30-2000 au looks for
MATCH_ARCSEC = 20.0
DISTANCE_TOL = 0.06  # the heliocentric distance of the body against the search's distance (its own range, plus this)
MAX_BODIES = 6
SB_VMAG = 26.0


def _sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    return math.hypot((ra1 - ra2) * math.cos(math.radians(0.5 * (dec1 + dec2))), dec1 - dec2) * 3600.0


_SB_MEMO: dict[
    tuple[float, float, float, float], list[dict[str, Any]]
] = {}  # JPL's answer per place, date and box (it takes up to a minute)


async def slow_bodies(client: httpx.AsyncClient, ra: float, dec: float, mjd: float, half_deg: float) -> list[dict[str, Any]]:
    """The slow bodies JPL puts in a box around a place at a date: name, V, rate (arcsec/hour), distance from the centre (arcsec)."""
    memo_key = (round(ra, 3), round(dec, 3), round(mjd, 1), round(half_deg, 2))
    if memo_key in _SB_MEMO:
        return _SB_MEMO[memo_key]
    found = await _slow_bodies(client, ra, dec, mjd, half_deg)
    if len(_SB_MEMO) > 500:
        _SB_MEMO.pop(next(iter(_SB_MEMO)))
    _SB_MEMO[memo_key] = found
    return found


async def _slow_bodies(client: httpx.AsyncClient, ra: float, dec: float, mjd: float, half_deg: float) -> list[dict[str, Any]]:
    r = await jpl.turn(
        lambda: client.get(
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
        ),
        lane="sbident",
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
    r = await jpl.turn(
        lambda: client.get(
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
    bodies: list[dict[str, Any]] | None = None
    for attempt in range(3):  # JPL's gateway fails one request in a few: a second and third ask costs less than an answer thrown away
        try:
            async with sem:
                bodies = await slow_bodies(client, first["ra"], first["dec"], mid, half)
            break
        except (httpx.HTTPError, ValueError, KeyError):
            await asyncio.sleep(3.0 * (attempt + 1))
    if bodies is None:
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


KINDS = ("deep", "distant")
KNOWN_MAX = 8  # leads of a result that are looked up (the strongest first)
FILE_VERSION = "k1"


def places_of(kind: str, result: dict[str, Any], c: dict[str, Any]) -> tuple[list[dict[str, Any]], float, float] | None:
    """The places of a lead to look up and the distance range the search gave (None: nothing to look up)."""
    if kind == "deep":
        # a deep result made before it said so itself has the windows of its passes in its plan (time order, as by_pass is)
        info = result.get("passes") or sorted((result.get("plan") or {}).get("passes") or [], key=lambda i: i.get("pass_mjd0", 0.0))
        by = c.get("by_pass") or []
        if not by or len(by) != len(info):
            return None
        places = [
            {"ra": q["ra"], "dec": q["dec"], "mjd": q.get("mjd"), "mjd0": i["pass_mjd0"], "mjd1": i["pass_mjd1"]}
            for q, i in zip(by, info, strict=True)
        ]
        return places, float(c["d"]), float(c["d"])
    if result.get("t0") is None or c.get("ra") is None:
        return None
    return [{"ra": c["ra"], "dec": c["dec"], "mjd": float(result["t0"])}], float(c.get("d_lo", c["d"])), float(c.get("d_hi", c["d"]))


class FpxIdentifier:
    """The JPL identification of the leads of every finished Find Planet X result, made ahead of time (a sweep over the store, one result at
    a time, at the lowest rate) and kept on disk, so a page that asks finds it at once.
    A failed answer (JPL down) is never kept; the definite ones are kept as they come."""

    def __init__(self, store: Any, directory: Path, client: httpx.AsyncClient | None = None, sweep_s: float = 60.0) -> None:
        self.store = store
        self.dir = directory
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), headers={"User-Agent": "spherex-sky/0.1"})
        self.sweep_s = sweep_s
        self.kick_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.limit = asyncio.Semaphore(2)
        self.failed_at: dict[tuple[str, str], float] = {}
        self.running: set[tuple[str, str]] = set()
        self.complete: set[tuple[str, str]] = set()

    def path(self, kind: str, key: str) -> Path:
        return self.dir / f"{kind}-{key}.{FILE_VERSION}.json"

    def cached(self, kind: str, key: str) -> dict[int, dict[str, Any]] | None:
        p = self.path(kind, key)
        if not p.is_file():
            return None
        return {int(i): v for i, v in json.loads(p.read_text()).items()}

    def annotate(self, kind: str, key: str, result: dict[str, Any]) -> dict[str, Any]:
        """The result with each lead's `known` (from the kept answers; `pending` while the sweep has not got to it)."""
        if kind not in KINDS or not result.get("candidates") or result.get("status") != "ok":
            return result
        found = self.cached(kind, key)
        if found is None:
            self.kick_event.set()
        out = dict(result)

        def with_known(i: int, c: dict[str, Any]) -> dict[str, Any]:
            if i >= KNOWN_MAX or not c.get("ok"):
                return c  # only the leads (peaks the photometry confirms) are looked up
            return {**c, "known": (found or {}).get(i, {"state": "pending"})}

        out["candidates"] = [with_known(i, c) for i, c in enumerate(result["candidates"])]
        return out

    def _keep(self, kind: str, key: str, found: dict[int, dict[str, Any]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path(kind, key).with_suffix(".part")
        tmp.write_text(json.dumps({str(i): v for i, v in sorted(found.items())}, separators=(",", ":")))
        tmp.replace(self.path(kind, key))

    async def identify_result(self, kind: str, key: str, result: dict[str, Any]) -> bool:
        """Looks up the leads of one result, strongest first, and keeps each definite answer as it comes (JPL's gateway fails one request
        in a few: an answer already got is never asked again). -> every lead has its answer."""
        t0 = time.monotonic()
        found = {i: v for i, v in (self.cached(kind, key) or {}).items() if v.get("state") in ("ok", "none")}
        whole = True
        asked = 0
        for i, c in enumerate((result.get("candidates") or [])[:KNOWN_MAX]):
            if not c.get("ok") or i in found:
                continue  # a peak the photometry does not confirm is not a lead: nothing to name
            pl = places_of(kind, result, c)
            if pl is None:
                found[i] = {"state": "none", "note": "the result does not say where the passes were"}
                self._keep(kind, key, found)
                continue
            asked += 1
            v = {**await identify(self.client, pl[0], pl[1], pl[2], self.limit), "at": [c.get("ra"), c.get("dec"), c.get("d")]}
            if v.get("state") == "unavailable":
                whole = False
                continue
            found[i] = v
            self._keep(kind, key, found)
        log.info("%s %s: %d leads asked in %.0f s, complete %s", kind, key[:8], asked, time.monotonic() - t0, whole)
        return whole and bool(found)

    async def sweep(self) -> int:
        """One pass over the finished results whose leads are not all answered, the deep sums first (few, and they hold the strongest
        leads). -> how many were completed."""
        n = 0
        rows = await asyncio.to_thread(self.store.listing, KINDS)
        rows.sort(key=lambda r: (r["kind"] != "deep", -float(r.get("created", 0.0))))  # the deep sums first, the newest first
        for row in rows:
            kind, key = row["kind"], row["key"]
            if not row["complete"] or (kind, key) in self.complete or (kind, key) in self.running:
                continue
            if time.monotonic() - self.failed_at.get((kind, key), -1e9) < 300.0:
                continue  # JPL did not answer a moment ago: not again at once
            meta = await asyncio.to_thread(self.store.read_meta, kind, key)
            if not meta or meta.get("status") != "ok" or not any(c.get("ok") for c in meta.get("candidates") or []):
                continue
            have = self.cached(kind, key) or {}
            need = [i for i, c in enumerate(meta["candidates"][:KNOWN_MAX]) if c.get("ok")]
            if need and all(have.get(i, {}).get("state") in ("ok", "none") for i in need):
                self.complete.add((kind, key))
                continue
            self.running.add((kind, key))
            try:
                if await self.identify_result(kind, key, meta):
                    self.complete.add((kind, key))
                    n += 1
                else:
                    self.failed_at[(kind, key)] = time.monotonic()
            finally:
                self.running.discard((kind, key))
        return n

    async def loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.sweep()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.kick_event.wait(), self.sweep_s)
            self.kick_event.clear()

    def start(self) -> None:
        self.task = asyncio.get_running_loop().create_task(self.loop())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.client.aclose()

    def enrich_findings(self, body: dict[str, Any]) -> dict[str, Any]:
        """The Findings answer with the JPL identification on its Find Planet X peaks: a peak that is a known body reads
        'this is Sedna (JPL), found blind' and counts as known."""
        changed = 0
        for f in body.get("finds", []):
            if f.get("kind") != "peak":
                continue
            pk = (f.get("data") or {}).get("peak") or {}
            scans = f.get("scans") or []
            kind = (f.get("data") or {}).get("search", "distant")
            found = self.cached(kind, scans[0]) if scans and kind in KINDS else None
            if found is None:
                continue
            for v in found.values():
                at = v.get("at") or [None, None, None]
                if at[0] is not None and abs(at[0] - f["ra"]) < 1e-4 and abs(at[1] - f["dec"]) < 1e-4:
                    pk["known"] = v
                    if v.get("state") == "ok":
                        if f.get("status") != "known":
                            changed += 1
                        f["status"], f["verdict"] = "known", v["words"][0].upper() + v["words"][1:] + "."
        if changed and isinstance(body.get("counts", {}).get("peak"), dict):
            c = body["counts"]["peak"]
            c["known"] = c.get("known", 0) + changed
            for k in ("unexplained", "unchecked"):
                take = min(changed, c.get(k, 0))
                c[k] = c.get(k, 0) - take
                changed -= take
        return body
