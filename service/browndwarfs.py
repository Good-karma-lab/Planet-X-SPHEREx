"""Brown dwarf candidates of a finished field: sources that are red at 3.8–5 µm against 1.1–1.6 µm, that move between the passes, and that
Gaia does not explain.

Evidence comes from two searches the field already has (nothing is read from the archive again):

    red     (service.red) the sources bright in D5–D6 and faint in D2, with the motion of their long-wavelength peak from pass to pass;
    movers  (service.movers) the white-stack tracks between passes; one that stands where a red source stands is a second, independent
            detection of the motion (its S/N per pass and fit rms are reported).

A moving red source is a candidate only when the catalogues do not explain it; one they explain is listed as known (a recovered brown
dwarf such as WISE 0855 or epsilon Indi B, or another named object). Gaia DR3 gives the verdict, as for every track of the movers search
(positions of Gaia stars moved to the pass dates):

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

from service import jsonsafe
from service.context import CatalogueContext
from service.movers import Movers, motion_grade, year_of
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
SIMBAD_RADIUS_ARCSEC = 8.0
BROWN_DWARF_TYPES = ("BD*", "Y*", "L*", "T*")


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


def red_movers(red: dict[str, Any], mjds: list[float], pass_index: list[int] | None = None) -> list[dict[str, Any]]:
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
            "path_rms_arcsec": r.get("path_rms_arcsec"),
            "path_passes": r.get("path_passes"),
        }
        used = [m for k, m in enumerate(mjds) if pass_index is None or r.get("path_passes") is None or pass_index[k] in r["path_passes"]]
        c.update(motion_grade(pm / 1000.0, r.get("path_rms_arcsec") or 0.0, used or mjds))
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
        **{
            k: t[k]
            for k in ("v_east", "v_north", "ra_2025_9", "dec_2025_9", "speed_err", "sigma", "shift_arcsec", "baseline_years", "grade")
            if k in t
        },
    }


def adopt_mover(c: dict[str, Any]) -> None:
    """A red source that the movers search found as well is one star measured twice: the motion of the track (measured on the white
    stacks of every pass, with the scatter of its places) is the one number shown; the red-band measurement is kept as `red_pm`."""
    m = c.get("mover")
    if not m or "v_east" not in m or "ra_2025_9" not in m:
        return
    c["red_pm"] = {k: c[k] for k in ("pmra", "pmdec", "pm", "pa", "ra", "dec")}
    c["pmra"], c["pmdec"] = round(m["v_east"] * 1000.0, 1), round(m["v_north"] * 1000.0, 1)
    c["pm"] = round(math.hypot(c["pmra"], c["pmdec"]), 1)
    c["pa"] = m["pa"]
    c.update({k: m[k] for k in ("speed_err", "sigma", "shift_arcsec", "baseline_years", "grade") if k in m})
    c["ra"], c["dec"] = m["ra_2025_9"], m["dec_2025_9"]
    k = EPOCH - 2000.0
    c["dec_j2000"] = c["dec"] - c["pmdec"] * k / 3.6e6
    c["ra_j2000"] = (c["ra"] - c["pmra"] * k / 3.6e6 / max(math.cos(math.radians(c["dec"])), 1e-6)) % 360.0


def status_of(c: dict[str, Any]) -> str:
    """What the source is, as far as the catalogues say: a known brown dwarf (recovered), another named object, a star Gaia explains,
    unidentified (a candidate), or not yet known (a catalogue did not answer)."""
    sb = c.get("simbad") or {}
    if sb.get("state") == "ok":
        return "known brown dwarf" if str(sb.get("otype", "")).startswith(BROWN_DWARF_TYPES) else "known object"
    if (c.get("gaia") or {}).get("verdict") == "same":
        return "star with Gaia motion"
    if sb.get("state") == "none":
        return "unidentified"
    return "catalogue did not answer"


def group_of(c: dict[str, Any]) -> str:
    """`known`: the catalogues explain it (SIMBAD names it, or a Gaia star moves with it); `candidate`: they do not and the motion is a
    mover's (at least 5 sigma and a pixel over the baseline); `slow`: they do not and the motion is below that (a possible slow motion: no
    candidate, no lead)."""
    if c["status"] not in ("unidentified", "catalogue did not answer"):
        return "known"
    return "slow" if c.get("grade") == "slow" else "candidate"


def movers_track(c: dict[str, Any], pass_index: list[int], mjds: list[float]) -> dict[str, Any]:
    """The motion of a moving red source in the shape of a track of the search between passes (the lab draws and lists both alike): its
    places at the dates of the passes it was measured in, along the straight line the peaks of the passes define. The signal is the red
    bands' (3.8-5 um), where the white stack does not see it, so there is no per-pass S/N."""
    t = c["gaia_check"]
    use = [k for k, pi in enumerate(pass_index) if c.get("path_passes") is None or pi in c["path_passes"]] or list(range(len(pass_index)))
    ve, vn = c["pmra"] / 1000.0, c["pmdec"] / 1000.0
    return {
        "passes": [pass_index[k] for k in use],
        "mjd": [round(mjds[k], 4) for k in use],
        "ra": [round(t["ra"][k], 6) for k in use],
        "dec": [round(t["dec"][k], 6) for k in use],
        "ra_2025_9": c["ra"],
        "dec_2025_9": c["dec"],
        "speed": round(math.hypot(ve, vn), 2),
        "pa": c["pa"],
        "v_east": round(ve, 3),
        "v_north": round(vn, 3),
        "rms_arcsec": c.get("path_rms_arcsec") if c.get("path_rms_arcsec") is not None else 0.0,
        "saturated": False,
        "snr": [None] * len(use),
        "check": t["check"],
        "gaia": t["gaia"],
        "source": "red",
        "colour": c["colour"],
        "snr_long": c["snr_long"],
        **{k: c[k] for k in ("speed_err", "sigma", "shift_arcsec", "baseline_years", "grade") if k in c},
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

    def __init__(self, store: Store, store_dir: Path, movers: Movers, catalogue: CatalogueContext | None = None) -> None:
        self.store = store
        self.store_dir = store_dir
        self.dir = store_dir / KIND
        self.movers = movers
        self.catalogue = catalogue
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
            cands = red_movers(red, mjds, [int(p["index"]) for p in meta["passes"]])[:MAX_CHECKED]
            for c in cands:
                c["mover"] = mover_match(c, tracks)
                adopt_mover(c)
                c["gaia_check"] = pseudo_track(c, mjds)
                c["bands"] = "colour = flux at 3.8–5 µm (D5–D6) / flux at 1.1–1.6 µm (D2)"
            run.result = {
                "field": run.field_key,
                "version": VERSIONS[KIND],
                "red_sources": red.get("sources"),
                "movers_checked": mrun.state == "done",
                "pass_index": [int(p["index"]) for p in meta["passes"]],
                "mjd": mjds,
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
        """Gaia and SIMBAD for every moving red source of the run; when both have answered for all, the sources are sorted into new
        candidates and known objects and the answer is kept."""
        items: list[dict[str, Any]] = run.result.get("candidates", [])
        pending = False
        for c in items:
            t = c["gaia_check"]
            if t["check"] == "pending":
                await self.movers.check_track(t)
            if t["check"] == "pending":
                pending = True
            c["gaia"] = {"verdict": t["check"], "text": verdict_text(t["check"]), **(t["gaia"] or {})}
            if "simbad" not in c or c["simbad"].get("state") == "unavailable":
                c["simbad"] = await self._simbad(c)
                if c["simbad"].get("state") == "unavailable":
                    pending = True
            c["status"] = status_of(c)
            c["track"] = movers_track(c, run.result.get("pass_index", []), run.result.get("mjd", []))
            c["track"]["status"] = c["status"]
            c["track"]["name"] = (c.get("simbad") or {}).get("name")
        run.result["pending"] = pending
        if pending:  # the pseudo tracks stay so that the catalogues are asked again; every source counts as a candidate for now
            return
        known = [c for c in items if group_of(c) == "known"]
        cands = [c for c in items if group_of(c) == "candidate"]
        slow = [c for c in items if group_of(c) == "slow"]
        for c in items:
            c.pop("gaia_check", None)
        run.result["candidates"], run.result["known"], run.result["slow"] = cands, known, slow
        run.result["counts"] = {
            "red_sources": run.result.get("red_sources"),
            "moving_red": len(items),
            "candidates": len(cands),
            "slow": len(slow),
            "known_brown_dwarfs": sum(1 for c in known if c["status"] == "known brown dwarf"),
            "known_other": sum(1 for c in known if c["status"] != "known brown dwarf"),
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f"{run.key}.part"
        tmp.write_text(jsonsafe.dumps(run.result, separators=(",", ":")))
        tmp.replace(self.dir / f"{run.key}.json")

    async def _simbad(self, c: dict[str, Any]) -> dict[str, Any]:
        """SIMBAD's answer at the candidate's place at 2025.9 (name, type, spectral type), or why there is none."""
        if self.catalogue is None:
            return {"state": "unavailable"}
        try:
            r = await self.catalogue.simbad_at(c["ra"], c["dec"], EPOCH, SIMBAD_RADIUS_ARCSEC)
        except Exception:  # the catalogue did not answer: asked again with the next request
            return {"state": "unavailable"}
        return {k: r[k] for k in ("state", "name", "otype", "sptype", "sep", "link") if k in r}

    def red_tracks(self, run: DwarfRun) -> list[dict[str, Any]]:
        """The tracks of the moving red sources the search between passes did not find itself (no track of the white stacks stands where
        the source stands), for the movers view. A source whose Gaia verdict is "still" (a static star's centroid shift) is left out."""
        out = []
        for c in (run.result.get("candidates") or []) + (run.result.get("known") or []):
            if c.get("mover") is None and c.get("track") and c["track"]["check"] != "still":
                out.append(c["track"])  # candidates are movers by construction; a known one is shown whatever its motion
        return out

    def view(self, run: DwarfRun) -> dict[str, Any]:
        out: dict[str, Any] = {"key": run.key, "state": run.state, "error": run.error}
        if run.result:
            r = dict(run.result)
            rows = [{k: v for k, v in c.items() if k != "gaia_check"} for c in r["candidates"]]
            if r.get("pending"):  # not sorted yet: a motion below a mover's is already only a possible slow motion
                r["slow"] = [c for c in rows if c.get("grade") == "slow"]
                rows = [c for c in rows if c.get("grade") != "slow"]
            r["candidates"] = rows
            out["result"] = r
        return out
