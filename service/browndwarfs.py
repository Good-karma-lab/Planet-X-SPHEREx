"""Brown dwarf candidates of a finished field: sources that are red at 3.8–5 µm against 1.1–1.6 µm, that move between the passes, and that
Gaia does not explain.

Evidence comes from two searches the field already has (nothing is read from the archive again):

    red     (service.red) the sources bright in D5–D6 and faint in D2, with the motion of their long-wavelength peak from pass to pass;
    movers  (service.movers) the white-stack tracks between passes; one that stands where a red source stands is a second, independent
            detection of the motion (its S/N per pass and fit rms are reported).

A candidate is a red source that moves. Gaia DR3 gives the verdict, as for every track of the movers search (positions of Gaia stars
moved to the pass dates):

    none     no Gaia star near the track: a candidate (a cold dwarf is too faint in the optical for Gaia)
    still    a Gaia star that hardly moves stands there: a candidate (the red light may belong to a neighbour; said so)
    same     a Gaia star with that proper motion explains it: not a candidate, listed as a known mover
    pending  Gaia did not answer: a candidate for now, asked again on the next request
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from service.movers import Movers, year_of
from service.products.store import Store, product_key
from service.products.versions import VERSIONS
from service.red import find_red_async
from service.workers import fieldjob

KIND = "browndwarfs"
MOVER_MATCH_ARCSEC = 4.0  # a track of the movers search stands where the red source stands (in the first pass of the track)
MIN_PM_MAS = 500.0  # a red source whose peaks shift less than this per year is not called a mover (centroid scatter)
EPOCH = 2025.9
MAX_CHECKED = 40  # Gaia is asked about this many candidates at most (strongest colour first)
WAIT_MOVERS_S = 600.0
CHECK_AGAIN_S = 60.0


@dataclass
class DwarfRun:
    key: str
    field_key: str
    state: str = "running"  # running | done | failed
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    checked_at: float = 0.0


def pseudo_track(c: dict[str, Any], mjds: list[float]) -> dict[str, Any]:
    """The straight track of a red source over the dates of the passes, in the shape the Gaia check of the movers search reads."""
    cosd = math.cos(math.radians(c["dec_j2000"]))
    ra, dec = [], []
    for m in mjds:
        t = year_of(m) - 2000.0
        d = c["dec_j2000"] + c["pmdec"] * t / 3.6e6
        ra.append(c["ra_j2000"] + c["pmra"] * t / 3.6e6 / max(cosd, 1e-6))
        dec.append(d)
    ve, vn = c["pmra"] / 1000.0, c["pmdec"] / 1000.0
    return {
        "mjd": list(mjds),
        "ra": ra,
        "dec": dec,
        "v_east": ve,
        "v_north": vn,
        "speed": math.hypot(ve, vn),
        "check": "pending",
        "gaia": None,
    }


def red_movers(red: dict[str, Any], mjds: list[float]) -> list[dict[str, Any]]:
    """The red sources of the field that move (peaks of the passes shift along a line faster than centroid scatter does)."""
    out = []
    for r in red.get("red", []):
        if not r.get("moves") or "pmra" not in r:
            continue
        pm = math.hypot(r["pmra"], r["pmdec"])
        if pm < MIN_PM_MAS:
            continue
        c = {
            "ra_j2000": r["ra_j2000"],
            "dec_j2000": r["dec_j2000"],
            "pmra": r["pmra"],
            "pmdec": r["pmdec"],
            "pm": round(pm, 1),
            "pa": round(math.degrees(math.atan2(r["pmra"], r["pmdec"])) % 360.0, 1),
            "colour": r["colour"],
            "lower_limit": bool(r["lower_limit"]),
            "snr_long": r["snr_long"],
            "snr_short": r["snr_short"],
        }
        k = EPOCH - 2000.0
        c["dec"] = round(c["dec_j2000"] + c["pmdec"] * k / 3.6e6, 6)
        c["ra"] = round((c["ra_j2000"] + c["pmra"] * k / 3.6e6 / max(math.cos(math.radians(c["dec_j2000"])), 1e-6)) % 360.0, 6)
        out.append(c)
    out.sort(key=lambda c: (-c["colour"], -c["snr_long"]))
    return out


def mover_match(c: dict[str, Any], tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The track of the movers search that stands where the red source stands (same place at the track's first pass), as evidence."""
    best: tuple[float, dict[str, Any]] | None = None
    cosd = math.cos(math.radians(c["dec_j2000"]))
    for t in tracks:
        if not t.get("mjd"):
            continue
        y = year_of(t["mjd"][0]) - 2000.0
        d = c["dec_j2000"] + c["pmdec"] * y / 3.6e6
        a = c["ra_j2000"] + c["pmra"] * y / 3.6e6 / max(cosd, 1e-6)
        sep = math.hypot((a - t["ra"][0]) * cosd, d - t["dec"][0]) * 3600.0
        if sep <= MOVER_MATCH_ARCSEC and (best is None or sep < best[0]):
            best = (sep, t)
    if best is None:
        return None
    t = best[1]
    return {
        "passes": t["passes"],
        "snr": t["snr"],
        "rms_arcsec": t["rms_arcsec"],
        "speed": t["speed"],
        "pa": t["pa"],
        "offset_arcsec": round(best[0], 2),
    }


def verdict_text(check: str) -> str:
    return {
        "none": "no Gaia star there",
        "still": "a Gaia star stands there but hardly moves",
        "same": "a Gaia star with this motion",
        "pending": "Gaia did not answer",
    }.get(check, check)


class BrownDwarfs:
    """Runs the brown dwarf search of fields, one run per field, kept on disk (as JSON) when Gaia answered for every candidate."""

    def __init__(self, store: Store, store_dir: Path, movers: Movers) -> None:
        self.store = store
        self.store_dir = store_dir
        self.dir = store_dir / KIND
        self.movers = movers
        self.runs: OrderedDict[str, DwarfRun] = OrderedDict()

    def key_of(self, field_key: str) -> str:
        return product_key(KIND, {"field": field_key})

    def get_or_start(self, field_key: str) -> DwarfRun:
        key = self.key_of(field_key)
        run = self.runs.get(key)
        if run is None:
            path = self.dir / f"{key}.json"
            if path.is_file():
                run = DwarfRun(key, field_key, state="done", result=json.loads(path.read_text()))
            else:
                run = DwarfRun(key, field_key)
                asyncio.get_running_loop().create_task(self._run(run))
            self.runs[key] = run
            while len(self.runs) > 200:
                self.runs.popitem(last=False)
        elif run.state == "done" and run.result.get("pending") and time.monotonic() - run.checked_at > CHECK_AGAIN_S:
            run.checked_at = time.monotonic()
            asyncio.get_running_loop().create_task(self._check_pending(run))
        return run

    async def _run(self, run: DwarfRun) -> None:
        try:
            meta = self.store.read_meta(fieldjob.KIND, run.field_key)
            if meta is None:
                raise ValueError("the field is not finished")
            red = await find_red_async(self.store, run.field_key, self.store_dir / "red")
            mrun = self.movers.get_or_start(run.field_key)
            waited = 0.0
            while mrun.state == "running" and waited < WAIT_MOVERS_S:  # the movers search is the second detection of a motion
                await asyncio.sleep(1.0)
                waited += 1.0
            tracks = list(mrun.result.get("tracks", [])) if mrun.state == "done" else []
            mjds = [float(p["mjd_mid"]) for p in meta["passes"]]
            cands = red_movers(red, mjds)[:MAX_CHECKED]
            for c in cands:
                c["mover"] = mover_match(c, tracks)
                c["gaia_check"] = pseudo_track(c, mjds)
                c["bands"] = "colour = flux at 3.8–5 µm (D5–D6) / flux at 1.1–1.6 µm (D2)"
            run.result = {
                "field": run.field_key,
                "version": VERSIONS[KIND],
                "red_sources": red.get("sources"),
                "movers_checked": mrun.state == "done",
                "candidates": cands,
                "known": [],
                "pending": True,
            }
            run.checked_at = time.monotonic()
            await self._check_pending(run)
            run.state = "done"
        except Exception as e:  # say why; the next request tries again
            run.state, run.error = "failed", str(e)[:300]
            self.runs.pop(run.key, None)

    async def _check_pending(self, run: DwarfRun) -> None:
        keep: list[dict[str, Any]] = []
        known: list[dict[str, Any]] = list(run.result.get("known", []))
        pending = False
        for c in run.result.get("candidates", []):
            t = c["gaia_check"]
            if t["check"] == "pending":
                await self.movers.check_track(t)
            if t["check"] == "pending":
                pending = True
            c["gaia"] = {"verdict": t["check"], "text": verdict_text(t["check"]), **(t["gaia"] or {})}
            if t["check"] == "same":
                known.append({k: c[k] for k in ("ra", "dec", "pm", "pa", "colour", "snr_long", "gaia")})
            else:
                keep.append(c)
        run.result["candidates"] = keep
        run.result["known"] = known
        run.result["pending"] = pending
        if not pending:
            for c in keep:
                c.pop("gaia_check", None)
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{run.key}.part"
            tmp.write_text(json.dumps(run.result, separators=(",", ":")))
            tmp.replace(self.dir / f"{run.key}.json")
        else:  # the pseudo tracks stay on the candidates so that Gaia is asked again; they are not part of the answer
            pass

    def view(self, run: DwarfRun) -> dict[str, Any]:
        out: dict[str, Any] = {"key": run.key, "state": run.state, "error": run.error}
        if run.result:
            r = dict(run.result)
            r["candidates"] = [{k: v for k, v in c.items() if k != "gaia_check"} for c in r["candidates"]]
            out["result"] = r
        return out
