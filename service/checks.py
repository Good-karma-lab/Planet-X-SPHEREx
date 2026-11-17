"""The checks before submit ("Check and submit"): a job that runs every check on a set of marks and keeps the answers.

    two kinds of movers, told apart by the marks: `fast` when every mark carries its own date and all are in one pass (a body that
    moves inside a pass, seen visit by visit), `slow` otherwise (it moves between passes). Below, the slow rules; for a fast mover
    two_passes wants three dated visits, straight_line fits a constant rate through them, above_bar reads the time-lapse track
    search, neowise does not apply and jpl (one ask per dated mark) decides.

    two_passes     the marks are in at least two passes
    straight_line  the marks lie on the line from the first to the last mark (largest distance from it against LINE_PASS / LINE_WARN)
    gaia           no Gaia DR3 star, carried to the date of each pass by proper motion and parallax, stands at the marks
    simbad         the same for SIMBAD objects
    jpl            no Solar System body of JPL's Small-Body Identification passes a mark during a visit of its pass
    edge_bright    the marks are not at the edge of the field and not on or beside a star that saturates SPHEREx
    above_bar      the machine's own search of this field has the track above its bar
    neowise        the NEOWISE archive (2014-2024): with an orbit, the W1 exposures added along it (service.checks_neowise, minutes);
                   without one, the single-exposure source table along the straight path of the marks carried back

Each check answers `pass` | `warn` | `fail` | `no_answer` with one plain sentence; a catalogue or archive that did not answer is
`no_answer`, never `pass`. `can_submit` is true only when every check has answered and none failed. The quick checks answer first; when
one of them fails the NEOWISE archive is not asked. A check has a time limit (no answer in time is `no_answer`); checks without an
answer are run again by the job itself every RETRY_S (`next_try_at` in the body), and by anyone posting the same request. Jobs are
kept on disk from their start: `resume()` at the service's start runs the interrupted ones again. When a job reaches its final
state `st.on_checks_done(body)` is called if set.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import importlib
import io
import json
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np

from engine.grid import Grid
from engine.movers import EDGE_ARCSEC as FIELD_EDGE_ARCSEC
from engine.movers import NSIG
from engine.onoff import TRACK_NSIG
from service import checks_neowise, jsonsafe
from service.context import BRIGHT_MAG, BRIGHT_RADIUS, display_name
from service.known_field import GAIA_EPOCH, SIMBAD_EPOCH, KnownField, cache_dir, epoch_year, offsets_arcsec, propagate
from service.storage import digest

VERSION = "c2"  # the rules' version: part of the job id, so answers made under older rules are not served
CHECK_IDS = ("two_passes", "straight_line", "gaia", "jpl", "simbad", "edge_bright", "above_bar", "neowise")
ANSWERED = ("pass", "warn", "fail")
MATCH_ARCSEC = 4.0  # a catalogue object this close to a mark (two thirds of a SPHEREx pixel) is the marked point
MIN_SHIFT_ARCSEC = 2.0  # marks closer together than this do not show a motion
LINE_PASS_ARCSEC, LINE_WARN_ARCSEC = 3.1, 6.2  # half a SPHEREx pixel, one pixel: a mark's distance from the line first-last
HALO_ARCSEC, HALO_BRIGHT_ARCSEC, HALO_BRIGHT_MAG = 60.0, 120.0, 6.0  # beside a saturated star: ghosts and diffraction spikes
JPL_MATCH_ARCSEC = 12.0  # two pixels: the mark is put on a pass image in which a body trails
JPL_MARGIN_RATE = 40.0  # ″/h: a fast main-belt body; the identification field is widened by this rate over half the pass
JPL_MAX_HALF_DEG = 1.0
JPL_MAX_ASKS = 24  # identifications of one job (JPL allows 20 in five minutes)
VISIT_GAP_DAYS = 0.6  # exposures of a place closer in time than this are one visit
JPL_TRIES, JPL_RETRY_S = 3, 20.0
TRACK_MATCH_ARCSEC = 6.2
STAR_MAX_ARCSEC_YR = 30.0  # faster than any star (Barnard's Star: 10.4): a straight path is not carried back ten years
NEOWISE_MATCH_ARCSEC = 3.0
NEOWISE_MIN_EPOCHS = 3
NEOWISE_FROM, NEOWISE_TO = 2014.0, 2024.6
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
MAX_MARKS = 40
QUICK = ("two_passes", "straight_line", "gaia", "simbad", "edge_bright", "above_bar")  # answered first, within seconds
LIMIT_S = {"jpl": 1800.0, "neowise": 5400.0}  # a check that takes longer ends in no_answer
QUICK_LIMIT_S = 180.0
RETRY_S, MAX_TRIES = 600.0, 12  # checks without an answer are run again by the job itself this often, this many times
FAST_MIN_VISITS = 3  # a fast mover (inside one pass) needs this many dated visits
FAST_CURVE_MIN_VISITS = 5  # a curved path is taken (with a warning) only with this many visits
ORBIT_RMS_MAX = 2.0  # the track search's own limit for a track fitted by an orbit (engine.filmsearch.RMS_MAX)
VISIT_SAME_DAYS = 0.02  # dates closer than half an hour are one visit

Answer = tuple[str, str]
Mark = dict[str, Any]


def _sep(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float]:
    east, north = offsets_arcsec(np.array([ra]), np.array([dec]), ra0, dec0)
    return float(east[0]), float(north[0])


def off_line(marks: list[Mark]) -> float:
    """The largest distance (arcsec) of a mark from the line through the first and the last mark in time."""
    ms = sorted(marks, key=lambda m: float(m["mjd"]))
    a, b = ms[0], ms[-1]
    east, north = _sep(b["ra"], b["dec"], a["ra"], a["dec"])
    length = math.hypot(east, north)
    if length == 0:
        return 0.0
    worst = 0.0
    for m in ms[1:-1]:
        e, n = _sep(m["ra"], m["dec"], a["ra"], a["dec"])
        worst = max(worst, abs(e * north - n * east) / length)
    return worst


def motion(marks: list[Mark]) -> tuple[float, float, float] | None:
    """(shift in arcsec, rate in arcsec/yr, direction in degrees east of north) from the first to the last mark in time; None when
    they are in one pass."""
    if not marks:
        return None
    ms = sorted(marks, key=lambda m: float(m["mjd"]))
    a, b = ms[0], ms[-1]
    years = (float(b["mjd"]) - float(a["mjd"])) / 365.25
    if years <= 0:
        return None
    east, north = _sep(b["ra"], b["dec"], a["ra"], a["dec"])
    shift = math.hypot(east, north)
    return shift, shift / years, math.degrees(math.atan2(east, north)) % 360.0


def kind_of(marks: list[dict[str, Any]]) -> str:
    """`fast`: two or more marks, every one with its own date, all in one pass (a body that moves inside a pass, seen visit by visit);
    `slow` otherwise (a body that moves between passes)."""
    if len(marks) >= 2 and all(m.get("mjd") is not None for m in marks) and len({int(m["pass"]) for m in marks}) == 1:
        return "fast"
    return "slow"


def visits_of(marks: list[Mark]) -> int:
    """Distinct dated visits among the marks (dates closer than VISIT_SAME_DAYS are one visit)."""
    days = sorted(float(m["mjd"]) for m in marks)
    return sum(1 for k, t in enumerate(days) if k == 0 or t - days[k - 1] > VISIT_SAME_DAYS)


def check_fast_visits(marks: list[Mark]) -> Answer:
    n = visits_of(marks)
    if n >= FAST_MIN_VISITS:
        hours = (max(float(m["mjd"]) for m in marks) - min(float(m["mjd"]) for m in marks)) * 24.0
        return "pass", f"A fast mover: marked in {n} dated visits of one pass, over {hours:.1f} hours."
    return "fail", f"A fast mover (all marks in one pass) needs at least {FAST_MIN_VISITS} dated visits; these marks have {n}."


def fit_motion(marks: list[Mark], degree: int) -> tuple[float, float, float]:
    """Places against time fitted by a polynomial of `degree` on each axis: (largest residual in arcsec, rate in arcsec/h at the
    middle, direction in degrees east of north)."""
    ms = sorted(marks, key=lambda m: float(m["mjd"]))
    t = np.array([float(m["mjd"]) for m in ms])
    t = (t - t.mean()) * 24.0
    east, north = offsets_arcsec(np.array([m["ra"] for m in ms]), np.array([m["dec"] for m in ms]), ms[0]["ra"], ms[0]["dec"])
    ce, cn = np.polyfit(t, east, degree), np.polyfit(t, north, degree)
    resid = float(np.hypot(east - np.polyval(ce, t), north - np.polyval(cn, t)).max())
    ve, vn = float(ce[-2]), float(cn[-2])
    return resid, math.hypot(ve, vn), math.degrees(math.atan2(ve, vn)) % 360.0


def check_fast_line(marks: list[Mark], searches: list[dict[str, Any]] | None = None) -> Answer:
    n = visits_of(marks)
    if n < FAST_MIN_VISITS:
        return "fail", f"A line at a constant rate needs at least {FAST_MIN_VISITS} dated visits; these marks have {n}."
    resid, rate, pa = fit_motion(marks, 1)
    said = f"{rate:.1f}″/h toward {pa:.0f}°"
    if resid <= LINE_PASS_ARCSEC:
        return "pass", f"The {n} visits lie on one line at a constant rate within {resid:.1f}″ ({said})."
    if n >= FAST_CURVE_MIN_VISITS:
        curved, rate2, pa2 = fit_motion(marks, 2)
        if curved <= LINE_PASS_ARCSEC:
            return (
                "warn",
                f"Not a straight line at a constant rate ({resid:.0f}″ off), but the {n} visits lie on a smooth curve within {curved:.1f}″ "
                f"({rate2:.1f}″/h toward {pa2:.0f}° at the middle): the path of a near body bends over days.",
            )
    found = best_track(marks, searches)
    if (
        found is not None
        and found[0] == len(marks)
        and found[1].get("method") == "orbit"
        and float(found[1].get("rms") or 99.0) <= ORBIT_RMS_MAX
    ):
        t = found[1]
        return (
            "warn",
            f"Not a straight line at a constant rate ({resid:.0f}″ off over {float(t.get('hours') or 0.0):.0f} hours), but the time-lapse "
            f"track search fits all {n} visits with one orbit within {float(t['rms']):.2f}″ ({float(t.get('rate') or 0.0):.1f}″/h): "
            "the path of a near body bends.",
        )
    state = "warn" if resid <= LINE_WARN_ARCSEC else "fail"
    return state, f"A visit is {resid:.1f}″ off the line at a constant rate through the others ({said})."


def best_track(marks: list[Mark], searches: list[dict[str, Any]] | None) -> tuple[int, dict[str, Any]] | None:
    """The track of the time-lapse track searches that has the most marks (same visit, within TRACK_MATCH_ARCSEC): (marks, track)."""
    best: tuple[int, dict[str, Any]] | None = None
    for meta in searches or []:
        g = meta["grid"]
        grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
        frames = meta.get("frames") or []
        for t in meta.get("tracks") or []:
            hit = 0
            for m in marks:
                for p in t.get("pts") or []:
                    f = int(p["f"])
                    if f >= len(frames) or abs(float(frames[f]["mjd"]) - float(m["mjd"])) > VISIT_SAME_DAYS:
                        continue
                    ra, dec = grid.grid_to_sky([p["x"]], [p["y"]])
                    if math.hypot(*_sep(float(ra[0]), float(dec[0]), m["ra"], m["dec"])) <= TRACK_MATCH_ARCSEC:
                        hit += 1
                        break
            if hit and (best is None or hit > best[0]):
                best = (hit, t)
    return best


def check_fast_bar(marks: list[Mark], searches: list[dict[str, Any]] | None) -> Answer:
    """The time-lapse track search (filmsearch) of the place: a track that has the marks, with its own significance."""
    if not searches:
        return "no_answer", "The time-lapse track search has not run on this place and pass: there is no bar to compare with."
    best = best_track(marks, searches)
    bar = f"{TRACK_NSIG:.0f}σ for the visits together"
    if best is None:
        return "warn", f"The time-lapse track search has no track above its bar ({bar}) at the marks: seen by eye only."
    hit, t = best
    z = float(t.get("z_together") or 0.0)
    state = "pass" if z >= TRACK_NSIG else "warn"
    checked = "verified in the exposures" if t.get("verified") else "not verified in the exposures"
    return (
        state,
        f"The time-lapse track search has this track at {hit} of {len(marks)} marks: {z:.1f}σ together ({checked}); the bar: {bar}.",
    )


def check_two_passes(marks: list[Mark]) -> Answer:
    passes = sorted({int(m["pass"]) for m in marks})
    if len(passes) >= 2:
        return "pass", f"Marked in {len(passes)} passes."
    if not marks:
        return "fail", "No marks."
    return "fail", "Marked in one pass only: a point seen once can be a cosmic ray or a detector defect."


def check_straight_line(marks: list[Mark]) -> Answer:
    passes = {int(m["pass"]) for m in marks}
    mo = motion(marks)
    if len(passes) < 2 or mo is None:
        return "fail", "A line needs marks in at least two passes."
    shift, rate, pa = mo
    said = f"{shift:.1f}″ between the first and the last pass, {rate:.2f}″/yr toward {pa:.0f}°"
    if shift < MIN_SHIFT_ARCSEC:
        return "warn", f"The marks do not move: {said} (under {MIN_SHIFT_ARCSEC:.0f}″)."
    if len(passes) < 3:
        return "warn", f"Two passes always lie on one line ({said}): a third pass would test it."
    off = off_line(marks)
    if off <= LINE_PASS_ARCSEC:
        return "pass", f"The marks of {len(passes)} passes lie on one line within {off:.1f}″ ({said})."
    state = "warn" if off <= LINE_WARN_ARCSEC else "fail"
    return state, f"A mark is {off:.1f}″ off the line through the others ({said})."


def distances(rows: list[list[Any]], cat_epoch: float, marks: list[Mark]) -> np.ndarray:
    """Distance (arcsec) of every catalogue row [id, ra, dec, pmra, pmdec, parallax, ...], carried to the date of each mark, from
    that mark: (rows, marks)."""
    a = np.array([r[1:6] for r in rows], float).reshape(-1, 5)
    pr, pd = propagate(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], cat_epoch, [float(m["mjd"]) for m in marks])
    out = np.empty((len(rows), len(marks)))
    for k, m in enumerate(marks):
        east, north = offsets_arcsec(pr[:, k], pd[:, k], m["ra"], m["dec"])
        out[:, k] = np.hypot(east, north)
    return out


def check_catalogue(part: dict[str, Any], marks: list[Mark], cat_epoch: float, label: Callable[[list[Any]], str]) -> Answer:
    """`fail` when one catalogue object stands at every mark, `warn` when at some of them."""
    if part.get("state") != "ok":
        return "no_answer", f"The catalogue did not answer ({part.get('error') or part.get('state')}): asked again on the next run."
    rows = part["rows"]
    if not rows:
        return "pass", "No catalogue object in the field of the marks."
    d = distances(rows, cat_epoch, marks)
    near = d <= MATCH_ARCSEC
    follows = np.flatnonzero(near.all(axis=1))
    if len(follows):
        i = int(follows[np.argmin(d[follows].max(axis=1))])
        where = "every mark" if len(marks) > 1 else "the mark"
        return "fail", f"{label(rows[i])} stands {d[i].max():.1f}″ from {where} at the date of its pass: a known object."
    some = np.flatnonzero(near.any(axis=1))
    if len(some):
        i = int(some[np.argmin(d[some].min(axis=1))])
        ks = sorted({int(marks[k]["pass"]) for k in np.flatnonzero(near[i])})
        return "warn", f"{label(rows[i])} is {d[i].min():.1f}″ from the mark of pass {', '.join(map(str, ks))}, not from the others."
    return (
        "pass",
        f"None of {len(rows)} catalogue objects around the marks is within {MATCH_ARCSEC:.0f}″ of a mark at the date of its pass.",
    )


def gaia_mag(r: list[Any]) -> float | None:
    return None if r[6] is None else float(r[6])


def simbad_mag(r: list[Any]) -> float | None:
    return next((float(x) for x in r[7:10] if x is not None), None)


def gaia_label(r: list[Any]) -> str:
    g = "" if r[6] is None else f"G {r[6]:.1f}, "
    return f"Gaia DR3 {r[0]} ({g}{math.hypot(r[3], r[4]) / 1000.0:.2f}″/yr)"


def simbad_label(r: list[Any]) -> str:
    return f"SIMBAD {display_name(str(r[0]), [])} ({r[6] or 'no type'}, {math.hypot(r[3], r[4]) / 1000.0:.2f}″/yr)"


def check_edge_bright(marks: list[Mark], grid: dict[str, Any] | None, gaia: dict[str, Any], simbad: dict[str, Any]) -> Answer:
    notes = []
    if grid is not None:
        half = grid["n"] * grid["step_arcsec"] / 2.0
        east, north = offsets_arcsec(np.array([m["ra"] for m in marks]), np.array([m["dec"] for m in marks]), grid["ra0"], grid["dec0"])
        margin = float((half - np.maximum(np.abs(east), np.abs(north))).min())
        if margin < FIELD_EDGE_ARCSEC:
            return (
                "fail",
                f"A mark is {max(margin, 0.0):.0f}″ from the field's edge (under {FIELD_EDGE_ARCSEC:.0f}″): the search does not reach it.",
            )
        notes.append(f"{margin:.0f}″ from the edge of the field")
    else:
        notes.append("the field is not in the store, so its edge was not checked")
    if gaia.get("state") != "ok" and simbad.get("state") != "ok":
        return "no_answer", "Neither Gaia nor SIMBAD answered: the bright stars around the marks are not known yet."
    worst: tuple[float, str] | None = None  # the bright star whose halo reaches a mark: (distance, name)
    parts: tuple[tuple[dict[str, Any], float, Callable[[list[Any]], float | None], Callable[[list[Any]], str]], ...] = (
        (gaia, GAIA_EPOCH, gaia_mag, gaia_label),
        (simbad, SIMBAD_EPOCH, simbad_mag, simbad_label),
    )
    for part, epoch, mag_of, name_of in parts:
        if part.get("state") != "ok":
            continue
        rows = [r for r in part["rows"] if (mag_of(r) or 99.0) <= BRIGHT_MAG]
        if not rows:
            continue
        d = distances(rows, epoch, marks).min(axis=1)
        for r, dist in zip(rows, d, strict=True):
            reach = HALO_BRIGHT_ARCSEC if (mag_of(r) or 99.0) <= HALO_BRIGHT_MAG else HALO_ARCSEC
            if dist <= reach and (worst is None or dist < worst[0]):
                worst = (float(dist), name_of(r))
    if worst is not None:
        dist, name = worst
        if dist <= BRIGHT_RADIUS:
            return "fail", f"A mark is {dist:.0f}″ from {name}, a star that saturates SPHEREx: its core and halo make false points."
        return "warn", f"A mark is {dist:.0f}″ from {name}, a star that saturates SPHEREx: look for a ghost or a diffraction spike."
    notes.append(f"no star brighter than magnitude {BRIGHT_MAG:.0f} within {HALO_ARCSEC:.0f}″")
    notes.extend(f"{n} did not answer" for n, p in (("Gaia", gaia), ("SIMBAD", simbad)) if p.get("state") != "ok")
    text = ", ".join(notes) + "."
    return "pass", text[:1].upper() + text[1:]


def track_miss(body: list[Any], mark: Mark, t0: float, t1: float, mid: float) -> float:
    """The closest a body [name, ra, dec, V, ra rate, dec rate (″/h, on the sky)] at `mid` comes to the mark between t0 and t1 (MJD),
    on a straight track: arcsec."""
    x, y = _sep(body[1], body[2], mark["ra"], mark["dec"])
    vx, vy = float(body[4] or 0.0), float(body[5] or 0.0)
    lo, hi = (t0 - mid) * 24.0, (t1 - mid) * 24.0
    v2 = vx * vx + vy * vy
    t = min(max(-(x * vx + y * vy) / v2, lo), hi) if v2 > 0 else 0.0
    return math.hypot(x + vx * t, y + vy * t)


def bar_words(square: dict[str, Any] | None) -> str:
    """The bar in words: the one the sweep stored for the square when it searched it, else the movers search's own."""
    bar = (square or {}).get("bar") or {}
    if bar.get("kind") == "sweep" and bar.get("mover_sigma") is not None:
        shift = f" and a shift of {bar['mover_shift_arcsec']:g}″" if bar.get("mover_shift_arcsec") is not None else ""
        return f"square {square['id']}: a motion of {bar['mover_sigma']:g}σ{shift} over {bar.get('passes', '?')} passes" if square else ""
    return f"{NSIG:.0f}σ in each pass"


def check_above_bar(marks: list[Mark], result: dict[str, Any] | None, square: dict[str, Any] | None = None) -> Answer:
    if result is None:
        return "no_answer", "The machine has not searched this field yet: there is no bar to compare with."
    best: tuple[int, dict[str, Any]] | None = None
    for t in result.get("tracks") or []:
        hit = 0
        for m in marks:
            for p, a, d in zip(t.get("passes", []), t.get("ra", []), t.get("dec", []), strict=False):
                if int(p) == int(m["pass"]) and math.hypot(*_sep(a, d, m["ra"], m["dec"])) <= TRACK_MATCH_ARCSEC:
                    hit += 1
        if hit and (best is None or hit > best[0]):
            best = (hit, t)
    bar = bar_words(square)
    if best is None:
        return "warn", f"The machine's search of this field has nothing above its bar ({bar}) at the marks: seen by eye only."
    hit, t = best
    snr = [s for s in t.get("snr", []) if s is not None]
    if not snr:
        return "pass", f"The machine's search has this track at {hit} of {len(marks)} marks (a saturated star: no signal-to-noise)."
    return (
        "pass",
        f"The machine's search has this track at {hit} of {len(marks)} marks, {min(snr):.0f}–{max(snr):.0f}σ against a bar of {bar}.",
    )


def neowise_path(marks: list[Mark], rows: list[dict[str, float]]) -> Answer:
    """NEOWISE single-exposure sources [{ra, dec, mjd}] against the straight path of the marks carried back to their dates."""
    ms = sorted(marks, key=lambda m: float(m["mjd"]))
    a, b = ms[0], ms[-1]
    days = float(b["mjd"]) - float(a["mjd"])
    east, north = _sep(b["ra"], b["dec"], a["ra"], a["dec"])
    ve, vn = east / days, north / days  # arcsec/day
    on: set[int] = set()
    still: set[int] = set()
    for r in rows:
        e, n = _sep(r["ra"], r["dec"], a["ra"], a["dec"])
        dt = r["mjd"] - float(a["mjd"])
        k = int(r["mjd"] // 182.6)
        if math.hypot(e - ve * dt, n - vn * dt) <= NEOWISE_MATCH_ARCSEC:
            on.add(k)
        elif math.hypot(e, n) <= NEOWISE_MATCH_ARCSEC and math.hypot(ve * dt, vn * dt) > 2 * NEOWISE_MATCH_ARCSEC:
            still.add(k)
    if len(on) >= NEOWISE_MIN_EPOCHS:
        return (
            "pass",
            f"NEOWISE (2014–2024) has a source on the path of the marks carried back in {len(on)} half-year epochs: it moved then too.",
        )
    if len(still) >= NEOWISE_MIN_EPOCHS:
        return (
            "fail",
            f"NEOWISE (2014–2024) has a source standing still at the first mark in {len(still)} epochs, none along the path: not a mover.",
        )
    return (
        "warn",
        "NEOWISE (2014–2024) has no source along the path of the marks carried back: fainter than its single exposures (W1 about 15) "
        "or not real.",
    )


def neowise_orbit_answer(res: dict[str, Any], orbit: dict[str, Any]) -> Answer:
    years = res.get("years") or [2014, 2024]
    lost = int(res.get("frames_failed") or 0)
    total = int(res.get("frames") or 0)
    read = f"{total - lost} of {total} exposures used" if lost else f"all {total} exposures used"
    head = f"NEOWISE {years[0]:.0f}–{years[-1]:.0f}, {read}"
    if res.get("status") != "ok":
        why = {
            "no-frames": "too few exposures cover the orbit's strip",
            "few-epochs": "too few epochs could be made",
            "no-scale": "no flux scale could be made from CatWISE stars",
            "masked": "static sources cover the orbit's place in every epoch",
        }.get(str(res.get("status")), str(res.get("status")))
        return "warn", f"{head}: {why}, so the archive cannot say."
    said = (
        f"{head} in {res['epochs_used']} epochs added along the orbit ({res['epochs_unmasked']} not masked): "
        f"{res['flux_mjy']:+.4f} ± {res['err_mjy']:.4f} mJy"
    )
    sc = res.get("scan")
    if sc and "orbits" in sc:
        said += (
            f"; {sc['orbits']} orbits around it reach {sc['sn_at_place']:.1f}σ at the place, shifted places {sc['noise_median']:.1f}σ "
            f"(median) to {sc['noise_max']:.1f}σ, {sc['places_not_below']} of {sc['places']} not below it"
        )
    flux = float(orbit.get("flux_mjy") or 0.0)
    if res.get("ruled_out"):
        return "fail", f"{said}, where a body of {flux:.3f} mJy would stand at {res['would_be_sigma']:.1f}σ: ruled out."
    if res.get("seen"):
        return "pass", f"{said}: the archive shows a source along the orbit at {res['flux_mjy'] / res['err_mjy']:.1f}σ."
    if flux > 0:
        return (
            "warn",
            f"{said}; a body of {flux:.3f} mJy would stand at {res['would_be_sigma']:.1f}σ: the archive is not deep enough to decide.",
        )
    return "warn", f"{said}: nothing along the orbit, and no flux was given to compare with."


class Job:
    def __init__(self, job_id: str, request: dict[str, Any]) -> None:
        self.id = job_id
        self.request = request
        self.checks: dict[str, dict[str, Any]] = {c: {"id": c, "state": "running", "detail": ""} for c in CHECK_IDS}
        self.state = "running"
        self.started = time.time()
        self.seconds: float | None = None
        self.field = ""  # the store key the request's field resolved to
        self.tries = 0
        self.next_try_at: float | None = None  # when the checks without an answer are run again (epoch seconds)
        self.task: asyncio.Task[None] | None = None

    def kept(self) -> dict[str, Any]:
        return {"seconds": self.seconds, "field": self.field, "state": self.state, "tries": self.tries}

    def view(self) -> dict[str, Any]:
        checks = [dict(self.checks[c]) for c in CHECK_IDS]
        answered = all(c["state"] in ANSWERED for c in checks)
        return {
            "job": self.id,
            "field_key": self.request.get("field_key", ""),
            "field": self.field,
            "kind": kind_of(self.request.get("marks") or []),
            "ra": self.request.get("ra"),
            "dec": self.request.get("dec"),
            "state": self.state,
            "checks": checks,
            "can_submit": bool(self.state == "done" and answered and not any(c["state"] == "fail" for c in checks)),
            "needs_comment": [c["id"] for c in checks if c["state"] == "warn"],
            "next_try_at": None
            if self.next_try_at is None
            else datetime.fromtimestamp(self.next_try_at, UTC).isoformat(timespec="seconds"),
            "seconds": round(time.time() - self.started, 1) if self.state == "running" else self.seconds,
        }


class Checks:
    def __init__(self, st: Any, known: KnownField, client: httpx.AsyncClient | None = None) -> None:
        self.st = st
        self.known = known
        base = cache_dir(st.cfg.store_dir).parent
        self.dir = base / "checks"
        self.neowise_cache = base / "neowise"
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), headers={"User-Agent": "spherex-sky/0.1"})
        self.jobs: dict[str, Job] = {}
        self.orbit_check: Callable[..., dict[str, Any]] = checks_neowise.orbit_check
        self.auto_retry = True
        self.retries: set[asyncio.Task[None]] = set()

    # ---- jobs -------------------------------------------------------------------------------------------------------------------------
    def _load(self, job_id: str) -> Job | None:
        try:
            kept = json.loads((self.dir / f"{job_id}.json").read_text())
        except (OSError, ValueError):
            return None
        job = Job(job_id, kept["request"])
        job.checks = {c: kept["checks"].get(c) or job.checks[c] for c in CHECK_IDS}
        job.state, job.seconds, job.field, job.tries = "done", kept.get("seconds"), kept.get("field", ""), int(kept.get("tries") or 0)
        if kept.get("state") == "running":  # the service stopped while it ran: the checks that were running have no answer yet
            for c in CHECK_IDS:
                if job.checks[c]["state"] not in ANSWERED:
                    job.checks[c] = {"id": c, "state": "no_answer", "detail": "The service restarted while this check ran: run again."}
        return job

    def _keep(self, job: Job) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{job.id}.part"
            tmp.write_text(jsonsafe.dumps({**job.kept(), "request": job.request, "checks": job.checks}, separators=(",", ":")))
            tmp.replace(self.dir / f"{job.id}.json")
        except OSError:
            pass

    def resume(self) -> int:
        """Called when the service starts (inside its loop): jobs on disk with checks that have no answer are run again."""
        n = 0
        for path in sorted(self.dir.glob("*.json")) if self.dir.is_dir() else []:
            job = self.get(path.stem)
            if job is not None and job.tries < MAX_TRIES and any(job.checks[c]["state"] not in ANSWERED for c in CHECK_IDS):
                self.start(job.request)
                n += 1
        return n

    def get(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id) or self._load(job_id)
        if job is not None:
            self.jobs[job_id] = job
        return job

    def start(self, request: dict[str, Any]) -> Job:
        """The job of a request (the same request is the same job); checks that have not answered are run (again)."""
        job_id = digest(VERSION, json.dumps(request, sort_keys=True))[:20]
        job = self.get(job_id) or Job(job_id, request)
        self.jobs[job_id] = job
        while len(self.jobs) > 500:
            self.jobs.pop(next(iter(self.jobs)))
        todo = [c for c in CHECK_IDS if job.checks[c]["state"] not in ANSWERED]
        if todo and (job.task is None or job.task.done()):
            for c in todo:
                job.checks[c] = {"id": c, "state": "running", "detail": ""}
            job.state, job.started, job.next_try_at = "running", time.time(), None
            job.tries += 1
            self._keep(job)  # a job that the service's restart interrupts is found again
            job.task = asyncio.get_running_loop().create_task(self._run(job, todo))
        return job

    async def _run(self, job: Job, todo: list[str]) -> None:
        req = job.request
        meta: dict[str, Any] | None = None
        store = getattr(self.st, "store", None)
        field = await asyncio.to_thread(self._resolve, req)
        job.field = field
        if store is not None and field:
            meta = await asyncio.to_thread(store.read_meta, "field", field)
        passes = {int(p["index"]): p for p in (meta or {}).get("passes", [])}
        marks: list[Mark] = []
        for m in req["marks"]:
            p = passes.get(int(m["pass"])) or {}
            own = m.get("mjd")
            dated_by = {"mjd": own if own is not None else p.get("mjd_mid"), "t0": own or p.get("mjd0"), "t1": own or p.get("mjd1")}
            marks.append({**m, **dated_by, "own_date": own is not None})
        dated = bool(marks) and all(m["mjd"] is not None for m in marks)
        ra, dec, size = float(req["ra"]), float(req["dec"]), 6.0
        if marks:
            ra, dec = float(np.mean([m["ra"] for m in marks])), float(np.mean([m["dec"] for m in marks]))
            east, north = offsets_arcsec(np.array([m["ra"] for m in marks]), np.array([m["dec"] for m in marks]), ra, dec)
            size = float(math.ceil(max(6.0, 2.0 * float(np.hypot(east, north).max()) / 60.0 + 4.0)))
        ra, dec = round(ra, 4), round(dec, 4)
        catalogues: dict[str, asyncio.Task[dict[str, Any]]] = {}

        def catalogue(name: str) -> asyncio.Task[dict[str, Any]]:
            if name not in catalogues:
                fetch = self.known.gaia if name == "gaia" else self.known.simbad
                catalogues[name] = asyncio.get_running_loop().create_task(fetch(ra, dec, size))
            return catalogues[name]

        blocked: Answer | None = None
        if not marks:
            blocked = ("fail", "No marks.")
        elif not dated:
            blocked = ("no_answer", "The dates of the passes are not known (the field is not in the store and the marks carry no date).")

        fast = kind_of(req["marks"]) == "fast"

        async def two_passes() -> Answer:
            return check_fast_visits(marks) if fast else check_two_passes(marks)

        async def straight_line() -> Answer:
            if blocked or not fast:
                return blocked or check_straight_line(marks)
            return check_fast_line(marks, await asyncio.to_thread(self._track_searches, marks))

        async def gaia() -> Answer:
            return blocked or check_catalogue(await catalogue("gaia"), marks, GAIA_EPOCH, gaia_label)

        async def simbad() -> Answer:
            return blocked or check_catalogue(await catalogue("simbad"), marks, SIMBAD_EPOCH, simbad_label)

        async def edge_bright() -> Answer:
            return blocked or check_edge_bright(marks, (meta or {}).get("grid"), await catalogue("gaia"), await catalogue("simbad"))

        async def jpl() -> Answer:
            return blocked or await self._jpl(job, marks)

        async def above_bar() -> Answer:
            if not marks:
                return "fail", "No marks."
            if fast:
                return check_fast_bar(marks, await asyncio.to_thread(self._track_searches, marks))
            square = await asyncio.to_thread(self._square, ra, dec)
            result = await asyncio.to_thread(self._movers, field)
            if result is None and square is not None:  # the field the sweep built for the square
                result = await asyncio.to_thread(self._movers, square.get("field_key"))
            return check_above_bar(marks, result, square)

        async def neowise() -> Answer:
            if fast and not req.get("orbit"):
                return "pass", "Not applicable to a body that moves this fast: a path of hours cannot be added in a ten-year archive."
            return await self._neowise(job, marks if dated else [], req.get("orbit"))

        runners: dict[str, Callable[[], Awaitable[Answer]]] = {
            "two_passes": two_passes,
            "straight_line": straight_line,
            "gaia": gaia,
            "jpl": jpl,
            "simbad": simbad,
            "edge_bright": edge_bright,
            "above_bar": above_bar,
            "neowise": neowise,
        }

        async def one(cid: str) -> None:
            limit = LIMIT_S.get(cid, QUICK_LIMIT_S)
            try:
                state, detail = await asyncio.wait_for(runners[cid](), limit)
            except TimeoutError:
                state, detail = "no_answer", f"No answer within {limit / 60.0:.0f} minutes: the check was stopped and is run again later."
            except Exception as e:  # a check that broke has not answered: said, and run again when asked again
                why = (str(e).splitlines() or [""])[0][:100]
                state, detail = "no_answer", f"The check could not be made ({type(e).__name__}: {why}): run again."
            job.checks[cid] = {**{k: v for k, v in job.checks[cid].items() if k == "result"}, "id": cid, "state": state, "detail": detail}

        # the quick checks first: they are in the body within seconds; JPL is asked meanwhile
        slow = asyncio.gather(*(one(c) for c in todo if c == "jpl"))
        await asyncio.gather(*(one(c) for c in todo if c in QUICK))
        failed = [c for c in QUICK if job.checks[c]["state"] == "fail"]
        if "neowise" in todo:
            if failed and not req.get("orbit"):  # an early fail blocks the submit anyway: the archive is not asked
                why = f"Not run: {', '.join(failed)} failed, which blocks the submit; the archive is asked once that is cleared."
                job.checks["neowise"] = {"id": "neowise", "state": "no_answer", "detail": why, "skipped": True}
            else:
                await one("neowise")
        await slow
        job.seconds = round(time.time() - job.started, 1)
        job.state = "done"
        waiting = [c for c in CHECK_IDS if job.checks[c]["state"] == "no_answer" and not job.checks[c].get("skipped")]
        if waiting and self.auto_retry and job.tries < MAX_TRIES:
            job.next_try_at = time.time() + RETRY_S
            for c in waiting:
                job.checks[c]["next_try_at"] = datetime.fromtimestamp(job.next_try_at, UTC).isoformat(timespec="seconds")

            async def again() -> None:
                await asyncio.sleep(RETRY_S)
                self.start(job.request)

            t = asyncio.get_running_loop().create_task(again())
            self.retries.add(t)
            t.add_done_callback(self.retries.discard)
        await asyncio.to_thread(self._keep, job)
        hook = getattr(self.st, "on_checks_done", None)
        if hook is not None and job.next_try_at is None:  # the final state: nothing more is tried by the job itself
            with contextlib.suppress(Exception):  # the hook is the accounts stream's: its failure is not the job's
                hook(job.view())

    def _resolve(self, req: dict[str, Any]) -> str:
        """The store key of the request's field. `field_key` is a store key, or the lab's "ra,dec,fov": the stored field of that size
        whose centre is nearest (within a twentieth of the size, the service's own snap); without a size, or with no key at all, the
        smallest stored field that holds every mark FIELD_EDGE_ARCSEC inside its edge. "" when the store has no such field."""
        raw = str(req.get("field_key") or "")
        if raw and "," not in raw and ":" not in raw:
            return raw  # a store key (a finding id such as "film:2e26…" names no field: the marks' place decides)
        store = getattr(self.st, "store", None)
        if store is None:
            return ""
        nums: list[float] = []
        for x in raw.split(","):
            try:
                nums.append(float(x))
            except ValueError:
                break
        fields = [r for r in store.listing(("field",)) if r["complete"] and {"ra", "dec", "fov"} <= r["params"].keys()]
        fields.reverse()  # the newest first: a field built again with more passes wins
        if len(nums) >= 3:
            ra, dec, fov = nums[:3]
            best: tuple[float, str] | None = None
            for r in fields:
                pp = r["params"]
                if abs(float(pp["fov"]) - fov) > 0.01:
                    continue
                dist = math.hypot(*_sep(float(pp["ra"]), float(pp["dec"]), ra, dec))
                if dist <= fov * 60.0 / 20.0 and (best is None or dist < best[0] - 1e-6):
                    best = (dist, str(r["key"]))
            return best[1] if best else ""
        places = [(m["ra"], m["dec"]) for m in req["marks"]] or [(float(req["ra"]), float(req["dec"]))]
        found: tuple[float, str] | None = None
        for r in fields:
            pp = r["params"]
            half = float(pp["fov"]) * 30.0 - FIELD_EDGE_ARCSEC
            if all(max(abs(v) for v in _sep(a, d, float(pp["ra"]), float(pp["dec"]))) <= half for a, d in places) and (
                found is None or float(pp["fov"]) < found[0]
            ):
                found = (float(pp["fov"]), str(r["key"]))
        return found[1] if found else ""

    def _track_searches(self, marks: list[Mark]) -> list[dict[str, Any]]:
        """The finished time-lapse track searches (filmsearch products) whose grid holds the first mark and whose frames cover its
        date."""
        store = getattr(self.st, "store", None)
        if store is None or not marks:
            return []
        m = marks[0]
        out = []
        for row in store.listing(("filmsearch",)):
            if not row["complete"]:
                continue
            meta = store.read_meta("filmsearch", row["key"])
            if not meta or not meta.get("grid") or not meta.get("frames"):
                continue
            g = meta["grid"]
            half = g["n"] * g["step_arcsec"] / 2.0
            if max(abs(v) for v in _sep(m["ra"], m["dec"], g["ra0"], g["dec0"])) > half:
                continue
            days = [float(f["mjd"]) for f in meta["frames"]]
            if min(days) - 1.0 <= float(m["mjd"]) <= max(days) + 1.0:
                out.append(meta)
        return out

    def _square(self, ra: float, dec: float) -> dict[str, Any] | None:
        """The grid square that holds a place, when the sweep has searched it: {id, bar, field_key, finds} (the grid stream's
        `service.grid` and `st.grid`); None when the grid is not there or the square is not searched."""
        db = getattr(self.st, "grid", None)
        if db is None:
            return None
        try:
            grid: Any = importlib.import_module("service.grid")
            cid = grid.cell_at(ra, dec).id
            row = db.get(cid)
        except Exception:
            return None
        if not row or row.get("state") != "searched":
            return None
        bar = row.get("bar")
        if isinstance(bar, str):
            try:
                bar = json.loads(bar)
            except ValueError:
                bar = None
        if hasattr(grid, "bar_at"):  # the grid stream's own reader of the stored bar
            with contextlib.suppress(Exception):
                bar = grid.bar_at(db, ra, dec) or bar
        return {"id": cid, "bar": bar if isinstance(bar, dict) else {}, "field_key": row.get("field_key"), "finds": row.get("finds")}

    def _movers(self, field_key: str | None) -> dict[str, Any] | None:
        movers = getattr(self.st, "movers", None)
        if movers is None or not field_key:
            return None
        try:
            out: dict[str, Any] = json.loads((self.st.cfg.store_dir / "movers" / f"{movers.key_of(field_key)}.json").read_text())
            return out
        except (OSError, ValueError):
            return None

    # ---- JPL --------------------------------------------------------------------------------------------------------------------------
    def _visits(self, mark: Mark) -> list[tuple[float, float]] | None:
        """The visits of a mark's pass: (first, last MJD) of the runs of exposures that hold the mark's place (runs are cut at gaps of
        VISIT_GAP_DAYS). A mark with its own date is one instant. None when the exposure index cannot say."""
        if mark.get("own_date"):
            return [(float(mark["mjd"]), float(mark["mjd"]))]
        index = getattr(self.st, "index", None)
        if index is None:
            return None
        spans = sorted((e.mjd0, e.mjd1) for e in index.containing(mark["ra"], mark["dec"], float(mark["t0"]), float(mark["t1"])))
        out: list[list[float]] = []
        for m0, m1 in spans:
            if out and m0 - out[-1][1] <= VISIT_GAP_DAYS:
                out[-1][1] = max(out[-1][1], m1)
            else:
                out.append([m0, m1])
        return [(v0, v1) for v0, v1 in out] or None

    async def _jpl(self, job: Job, marks: list[Mark]) -> Answer:
        """A pass lasts weeks and a body crosses the field in hours, so JPL is asked once per visit of each mark's pass (the exposures
        that hold the mark, from the index): the bodies around the mark at the middle of the visit, the field widened by how far a
        main-belt body goes in half the visit; a body whose straight track passes the mark during the visit is the marked point."""
        asks: list[tuple[Mark, float, float]] = []
        unknown = 0
        for m in marks:
            visits = await asyncio.to_thread(self._visits, m)
            if visits is None:  # the middle of the pass is all that can be asked
                unknown += 1
                visits = [(float(m["mjd"]), float(m["mjd"]))]
            asks.extend((m, v0, v1) for v0, v1 in visits)
        total = len(asks)
        asks = asks[:JPL_MAX_ASKS]
        hits: list[tuple[float, str]] = []
        seen = 0
        for n, (m, t0, t1) in enumerate(asks):
            mid = 0.5 * (t0 + t1)
            half = min(JPL_MAX_HALF_DEG, 2.0 / 60.0 + JPL_MARGIN_RATE * 12.0 * (t1 - t0) / 3600.0)
            part: dict[str, Any] = {}
            for attempt in range(JPL_TRIES):
                again = ", again" if attempt else ""
                job.checks["jpl"]["detail"] = f"Asking JPL for the bodies of visit {n + 1} of {len(asks)}{again}."
                part = await self.known.jpl_at(round(m["ra"], 4), round(m["dec"], 4), round(2.0 * half * 60.0, 1), round(mid, 3), None)
                if part.get("state") == "ok":
                    break
                await asyncio.sleep(JPL_RETRY_S)
            if part.get("state") != "ok":
                return "no_answer", f"JPL did not answer ({part.get('error') or part.get('state')}): asked again on the next run."
            seen += len(part["rows"])
            for body in part["rows"]:
                miss = track_miss(body, m, t0, t1, mid)
                if miss <= JPL_MATCH_ARCSEC:
                    rate = math.hypot(body[4] or 0.0, body[5] or 0.0)
                    v = "" if body[3] is None else f"V {body[3]:.1f}, "
                    hits.append((miss, f"{body[0]} ({v}{rate:.0f}″/h) passes {miss:.0f}″ from the mark of pass {int(m['pass'])}"))
        if hits:
            return "fail", f"JPL: {min(hits)[1]}: a known Solar System body."
        said = f"None of {seen} bodies JPL puts around the marks (V ≤ 20.5) passes within {JPL_MATCH_ARCSEC:.0f}″ of a mark"
        if total > len(asks):
            return "warn", f"{said} in the {len(asks)} visits asked, of {total}: the other visits were not asked."
        if unknown:
            return (
                "warn",
                f"{said} at the middle of its pass; the dates of the visits are not known, so the rest of the pass was not asked.",
            )
        return "pass", f"{said} in the {total} visits of the passes."

    # ---- NEOWISE ----------------------------------------------------------------------------------------------------------------------
    async def _neowise(self, job: Job, marks: list[Mark], orbit: dict[str, Any] | None) -> Answer:
        if orbit:

            def say(text: str) -> None:
                job.checks["neowise"]["detail"] = text[:1].upper() + text[1:] + "."

            res = await asyncio.to_thread(self.orbit_check, orbit, self.neowise_cache, say)
            job.checks["neowise"]["result"] = res
            return neowise_orbit_answer(res, orbit)
        mo = motion(marks)
        if mo is None:
            return "warn", "One pass and no orbit: there is no path to follow back into the NEOWISE years."
        _, rate, _ = mo
        if rate > STAR_MAX_ARCSEC_YR:
            return (
                "warn",
                f"The marks move {rate:.0f}″/yr, faster than any star: a Solar System body cannot be followed back ten years on a straight "
                "line, and no orbit was given. NEOWISE was not searched.",
            )
        ms = sorted(marks, key=lambda m: float(m["mjd"]))
        a, b = ms[0], ms[-1]
        east, north = _sep(b["ra"], b["dec"], a["ra"], a["dec"])
        span = (float(b["mjd"]) - float(a["mjd"])) / 365.25
        y0 = epoch_year(float(a["mjd"]))
        mid, half = 0.5 * (NEOWISE_FROM + NEOWISE_TO) - y0, 0.5 * (NEOWISE_TO - NEOWISE_FROM)
        ra = a["ra"] + east * mid / span / 3600.0 / max(math.cos(math.radians(a["dec"])), 1e-3)
        dec = a["dec"] + north * mid / span / 3600.0
        cones = [(ra, dec, (rate * half + 2 * NEOWISE_MATCH_ARCSEC) / 3600.0), (a["ra"], a["dec"], NEOWISE_MATCH_ARCSEC / 3600.0)]
        job.checks["neowise"]["detail"] = "Asking IRSA for the NEOWISE sources along the path."

        async def cone(x: float, y: float, radius: float) -> list[dict[str, float]]:
            # one cone per query: IRSA's TAP does not take two regions joined by OR
            q = (
                "SELECT ra, dec, mjd FROM neowiser_p1bs_psd WHERE qual_frame > 0 AND "
                f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {x:.6f}, {y:.6f}, {radius:.6f})) = 1"
            )
            r = await self.client.post(IRSA_TAP, data={"QUERY": q, "FORMAT": "csv", "LANG": "ADQL", "REQUEST": "doQuery"})
            r.raise_for_status()
            if r.text.lstrip().startswith("<"):
                raise ValueError("IRSA answered an error page")
            return [
                {"ra": float(v["ra"]), "dec": float(v["dec"]), "mjd": float(v["mjd"])}
                for v in csv.DictReader(io.StringIO(r.text))
                if v.get("mjd") and v.get("ra") and v.get("dec")
            ]

        found = await asyncio.gather(*(cone(*c) for c in cones))
        rows = [row for part in found for row in part]
        return neowise_path(marks, rows)
