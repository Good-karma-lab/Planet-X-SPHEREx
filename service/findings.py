# ruff: noqa: E501
"""Findings: every find of every searched field, kept in a database and identified against the catalogues.

The scans of a field (movers and pairs between passes, brown dwarfs, red sources, spectral anomalies, the film search, Find Planet X) each end in a
result file. `collect` reads them all and lists the finds; `Findings.sweep` writes them to `findings.db` (one row per find, the keys of its scan
products beside it) whenever a result file changed, so a scan that finishes is in the table within a minute and a field whose scans change is
refreshed. A small worker (`Findings.identify_loop`) asks every catalogue about every find: SIMBAD, Gaia DR3 (carried to the epoch with its proper
motion), 2MASS, AllWISE, VSX and NED at the place, and JPL's Small-Body Identification for the finds that move. One row per find and catalogue holds
what it said (`ok` with a name, separation and type, `none`, or `unavailable`: did not answer, asked again later). `/api/v1/findings` only reads.

The database is SQLite in WAL mode and shared by the two instances of a zero-downtime deploy: the schema is versioned (`meta.schema`), only the main
instance writes, and an instance that finds a newer schema than its own reads and leaves it alone.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import marshal
import math
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx

from service import cpu, people_tracks
from service.bodies import SB_IDENT, _iso, _sexa_dec, _sexa_ra
from service.config import hide_faint_peaks
from service.products.store import Store
from service.products.versions import VERSIONS
from service.storage import atomic_write_bytes
from service.upstream import jpl
from service.upstream.breaker import Breaker, BreakerOpenError

SCHEMA = 2
KINDS = ("mover", "browndwarf", "red", "anomaly", "pair", "film", "peak")
KIND_LABEL = {
    "mover": "Mover between passes",
    "browndwarf": "Brown dwarf",
    "red": "Red source",
    "anomaly": "Spectral anomaly",
    "pair": "Pair changing in place",
    "film": "Moving object in a pass (time-lapse search)",
    "peak": "Find Planet X candidate",
}
CATALOGUES = ("simbad", "gaia", "twomass", "allwise", "vsx", "ned")
CATALOGUE_LABEL = {
    "simbad": "SIMBAD",
    "gaia": "Gaia DR3",
    "twomass": "2MASS",
    "allwise": "AllWISE",
    "vsx": "VSX (variable stars)",
    "ned": "NED (galaxies)",
    "sbident": "JPL Small-Body Identification",
    "halo": "Bright star nearby (Gaia G < 10, within 60″)",
}
MOVING = (
    "film",
)  # asked of JPL Small-Body Identification: only tracks within one pass move like an asteroid (the movers between passes are stars)
COLLECT_REV = 7  # 7: film finds carry their field (field_params)
IDENT_RANK = {
    "peak": 0,
    "mover": 0,
    "browndwarf": 0,
    "film": 0,
    "pair": 1,
    "red": 2,
    "anomaly": 2,
}  # the order the catalogues are asked in  # bump when the way finds are read or merged changes: the next sweep rewrites them
SB_VMAG = 21.0  # SPHEREx depth: fainter bodies are not what a find could be
SB_TIMEOUT_S = 90.0  # JPL Small-Body Identification answers slowly (a field search, every body propagated)
SB_MATCH_ARCSEC = 15.0
SB_RATE_TOL = 0.25
SB_PA_TOL = 30.0
# a chain through a known body (chain_of): one visit of the track this close to a body that does not move like it, or two visits within
# SB_MATCH_ARCSEC of the same body. Of the 48 bodies beside a visit known on 23 Nov 2026, 47 stood under 5.5″ and one at 7.5″; chance
# would put 84 % of them past 6″
CHAIN_ARCSEC = 6.0
# a time-lapse track under this signal is not a lead: of 656 tracks on 23 Nov 2026 the 104 at or over it held 56 of the 60 known bodies,
# and the unexplained ones under it move like noise (25 % along the ecliptic against 33 % by chance; the known ones 83 %)
WEAK_Z = 7.0
PEAK_RATE_MAX = 15.0  # ″/h: a body of the outer solar system moves slower (service.fpx_known.SLOW_RATE)
GENERIC_TYPES = ("", "*", "Star", "IR", "Radio", "UV", "X", "Blue", "Red", "?")  # a SIMBAD type that explains nothing about a spectrum
EPOCH = 2025.9
ECLIPTIC_BAND = 15.0  # degrees of ecliptic latitude: a slow mover there is also checked against the known solar-system bodies


def ecl_lat(ra: float, dec: float) -> float:
    e = math.radians(23.4393)
    r, d = math.radians(ra), math.radians(dec)
    return math.degrees(math.asin(math.sin(d) * math.cos(e) - math.cos(d) * math.sin(e) * math.sin(r)))


def want_of(kind: str, ra: float, dec: float, data: dict[str, Any]) -> list[str]:
    """The identifications a find needs: a film track (it moves within a pass) only JPL's Small-Body Identification; the rest every catalogue, and
    a brown dwarf candidate near the ecliptic JPL as well (it must not be a known body before it is called a candidate)."""
    if kind == "film":
        return ["sbident"]
    near = kind == "browndwarf" and data.get("group") != "known" and abs(ecl_lat(ra, dec)) < ECLIPTIC_BAND
    return [*CATALOGUES, *(["sbident"] if near or _ref_mjd(kind, data) is not None else []), *(["halo"] if kind == "peak" else [])]


def _ref_mjd(kind: str, data: dict[str, Any]) -> float | None:
    """The date a Find Planet X peak's place is for (deep results made after the date fix carry it): JPL is asked only at that date."""
    return _f((data.get("peak") or {}).get("ref_mjd")) if kind == "peak" else None


def decisive_of(kind: str, ra: float, dec: float, data: dict[str, Any]) -> list[str]:
    """The catalogues that decide what a find is; the others only add detail (and are listed as "did not answer" when they did not).
    Film tracks and peaks: JPL for a moving body (peaks only when their date is known) and, for peaks, SIMBAD and Gaia for a star they sit on;
    movers, brown dwarfs, pairs, red sources and anomalies: SIMBAD and Gaia (a brown dwarf near the ecliptic JPL too)."""
    if kind == "film":
        return ["sbident"]
    out = ["simbad", "gaia"]
    wanted = want_of(kind, ra, dec, data)
    if "sbident" in wanted:
        out.append("sbident")
    if "halo" in wanted:
        out.append("halo")
    return out


RESIDUAL_ARCSEC = 3.0
HALO_ARCSEC = 60.0  # a Gaia star brighter than G 10 this close puts a peak in its halo
HALO_MAG = 10.0
# a 2MASS star of K <= 10 this far from a far-search peak: the peak is most likely the wing of its light outside the search's mask (the
# mask is made from the other passes, where the wing lies on the other side; 64 of 485 peaks on 26 Nov 2026, the first four Promising,
# against 0-2 in the control directions). Behind the faint filter, as the faint peaks are. No side in the rule: it turns with the season
WING_K = 10.0
WING_MIN_ARCSEC = 25.0
WING_MAX_ARCSEC = 60.0
WING_PAUSE_S = 0.5  # between two questions of one asker
WING_AT_ONCE = 4  # askers at a time: IRSA's sync TAP takes 5-20 s a question (26 Nov: 88 peaks in 25 min, one at a time)
WING_LOG_S = 600.0  # how often the loop says how far it is
WING_RETRY_S = 3600.0  # an unanswered one is asked again after this
WING_FIRST_S = 60.0  # the first question after a start: the start itself is busy enough
GALAXY_TYPES = frozenset(
    {
        "G",
        "GiG",
        "GiC",
        "GiP",
        "GiPair",
        "BiC",
        "rG",
        "H2G",
        "LSB",
        "bCG",
        "SBG",
        "IG",
        "PoG",
        "EmG",
        "Sy1",
        "Sy2",
        "SyG",
        "AGN",
        "QSO",
        "Bz",
        "BLL",
        "Bla",
        "LINER",
        "AG?",
        "Galaxy",
    }
)
GALAXY_NAMES = ("ACS-GC", "2MASX", "LEDA", "SDSS")  # catalogues of galaxies: a match to one is a galaxy, never a brown dwarf or a mover
HIDDEN = (
    "residual",
    "galaxy",
    "below",
    "halo",
    "set_aside",
    "blend",
    "unverified",
    "weak",
)  # statuses kept out of the leads and the default list


def is_galaxy(name: str | None, typ: str | None) -> bool:
    return (typ or "") in GALAXY_TYPES or (name or "").lstrip("[]* ").startswith(GALAXY_NAMES)


RADIUS = 6.0  # arcsec: how far from a find a catalogue entry may stand and still be named
# a mover or brown-dwarf candidate is asked this far: a catalogue source beyond RADIUS but within 1.5 SPHEREx pixels is its neighbour,
# blended with it in the picture; a claimed motion no larger than the gap is the centroid moving inside the blend, not a body
BLEND_ARCSEC = 10.0
BLEND_KINDS = ("mover", "browndwarf")
STILL_FACTOR = 3.0  # a survey source at today's place where the claimed motion would have carried it this many RADIUS away: it stood
SURVEY_EPOCH = {"allwise": 2010.5, "twomass": 1999.5}
SWEEP_S = 120.0  # a sweep reads every product file (each parse holds the GIL): new finds also kick it at once
RETRY_S = 90.0
MAX_ATTEMPTS = 8
EXTRA_RETRY_S = 6 * 3600.0  # a catalogue that only adds detail, past MAX_ATTEMPTS, is still asked this often (a card stops saying it did
# not answer once the service is back: VizieR's TAP did not answer VSX for days)
RESULT_DIRS = ("movers", "pairs", "browndwarfs", "red", "anomalies")


def _cell(ra: float, dec: float) -> str:
    c = math.cos(math.radians(dec))
    return f"{round(ra * c * 3600 / 6)}:{round(dec * 3600 / 6)}"


SLUG_WORD = {"film": "track"}  # the page's words for a kind, where the id's is a machine word ("film" is a time-lapse track)


def slug_of(fid: str) -> str:
    """A find's id in the page's words, for addresses and titles: film:8dcb8e38bc95 -> track-8dcb8e38bc95 (the id stays the key)."""
    kind, _, h = fid.partition(":")
    return f"{SLUG_WORD.get(kind, kind)}-{h}" if h else fid


def id_of(name: str) -> str:
    """A find's id from its id or its slug (track-8dcb8e38bc95 -> film:8dcb8e38bc95)."""
    if ":" in name or "-" not in name:
        return name
    word, _, h = name.rpartition("-")
    kind = next((k for k, w in SLUG_WORD.items() if w == word), word)
    return f"{kind}:{h}"


def find_id(kind: str, ra: float, dec: float, extra: str = "") -> str:
    return f"{kind}:{hashlib.sha1(f'{kind}{_cell(ra, dec)}{extra}'.encode()).hexdigest()[:12]}"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        d: dict[str, Any] = json.loads(path.read_text())
        return d
    except (OSError, ValueError):
        return None


def place_name(targets: list[dict[str, Any]], ra: float, dec: float) -> str:
    """The test target whose field this is (within half a degree), else the coordinates."""
    best = min(targets, key=lambda t: math.hypot((t["ra"] - ra) * math.cos(math.radians(dec)), t["dec"] - dec), default=None)
    if best is not None and math.hypot((best["ra"] - ra) * math.cos(math.radians(dec)), best["dec"] - dec) < 0.5:
        return str(best["n"])
    return f"{ra:.2f} {dec:+.2f}"


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def collect(
    store_dir: Path, rows: list[dict[str, Any]], fkeys: dict[str, dict[str, str]], targets: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    """Every find in the result files, and a signature of those files (when it is unchanged nothing has to be written).
    `rows`: the product rows of the store; `fkeys[field_key]`: the keys of the field's result files by search."""
    finds: dict[str, dict[str, Any]] = {}
    sig = hashlib.sha1()
    fp: dict[str, float] | None = None  # the field being read: its place and size, to open its grid again

    def seen(p: Path) -> dict[str, Any] | None:
        try:
            s = p.stat()
        except OSError:
            return None
        sig.update(f"{p.name}{s.st_mtime_ns}{s.st_size}".encode())
        return _read(p)

    def add(f: dict[str, Any]) -> None:
        if fp is not None and f["kind"] in ("mover", "pair", "browndwarf", "red", "anomaly"):
            f["data"]["field_params"] = dict(fp)
        f.update(extras(f))
        cur = finds.get(f["id"])
        if cur is None or f.get("fov", 0) > cur.get("fov", 0):
            if cur is not None:
                f["scans"] = sorted(set(f["scans"]) | set(cur["scans"]))
            finds[f["id"]] = f
        else:
            cur["scans"] = sorted(set(cur["scans"]) | set(f["scans"]))

    for row in rows:
        p = row["params"]
        # a field's finds come from its searches' result files (named by the field's key), not from its pictures: a field the sweep
        # stripped and that is being made again (not complete for that while, or for good if a restart cut its build) keeps its finds
        if row["kind"] == "field":
            k = fkeys[row["key"]]
            ra, dec, fov = float(p["ra"]), float(p["dec"]), float(p.get("fov", 5))
            place = place_name(targets, ra, dec)
            fp = {"ra": ra, "dec": dec, "fov": fov}
            scans = {"field": row["key"]}
            mv = seen(Path(store_dir) / "movers" / f"{k['movers']}.json")
            if mv:
                scans["movers"] = k["movers"]
                for t in mv.get("tracks", []):
                    if t.get("check") == "still" or (t.get("grade") == "slow" and t.get("check") != "same"):
                        continue
                    tr_ra, tr_dec = _f(t.get("ra_2025_9")), _f(t.get("dec_2025_9"))
                    if tr_ra is None or tr_dec is None:
                        continue
                    snr = [s for s in (t.get("snr") or []) if s is not None]
                    add(
                        {
                            "id": find_id("mover", tr_ra, tr_dec),
                            "kind": "mover",
                            "place": place,
                            "fov": fov,
                            "ra": tr_ra,
                            "dec": tr_dec,
                            "rate": _f(t.get("speed")),
                            "snr": min(snr) if snr else None,
                            "score": _f(t.get("sigma")),
                            "chance": _f((mv.get("chance") or {}).get("mean")),
                            "data": {
                                "track": t,
                                "passes": mv.get("passes"),
                                "mjd": mv.get("mjd"),
                                "chance": mv.get("chance"),
                                "field": mv.get("field"),
                            },
                            "scans": [v for v in (scans["field"], k["movers"]) if v],
                        }
                    )
                for pr in mv.get("pairs", []):
                    comps = pr.get("components") or []
                    if len(comps) < 2:
                        continue
                    pra = sum(c["ra"] for c in comps) / len(comps)
                    pdec = sum(c["dec"] for c in comps) / len(comps)
                    add(
                        {
                            "id": find_id("pair", pra, pdec),
                            "kind": "pair",
                            "place": place,
                            "fov": fov,
                            "ra": pra,
                            "dec": pdec,
                            "rate": None,
                            "snr": min((c.get("snr") or 0) for c in comps) or None,
                            "score": _f(pr.get("change_sigma")),
                            "chance": _f((mv.get("chance_pairs") or {}).get("expected")),
                            "data": {
                                "pair": {k2: v for k2, v in pr.items() if k2 != "crops"},
                                "field": mv.get("field"),
                                "mjd": mv.get("mjd"),
                            },
                            "scans": [scans["field"], k["movers"]],
                        }
                    )
            bd = seen(Path(store_dir) / "browndwarfs" / f"{k['browndwarfs']}.json")
            if bd:
                for group, known in (
                    ("candidates", False),
                    ("known", True),
                ):  # "slow" ones are a possible slow motion, not candidates: left out
                    for c in bd.get(group, []):
                        cra, cdec = _f(c.get("ra")), _f(c.get("dec"))
                        if cra is None or cdec is None:
                            continue
                        add(
                            {
                                "id": find_id("browndwarf", cra, cdec),
                                "kind": "browndwarf",
                                "place": place,
                                "fov": fov,
                                "ra": cra,
                                "dec": cdec,
                                "rate": (_f(c.get("pm")) or 0.0) / 1000.0
                                if c.get("pm") is not None
                                else None,  # the scan gives mas/yr; shown in ″/yr
                                "snr": _f(c.get("snr_long")),
                                "score": _f(c.get("sigma")),
                                "chance": None,
                                "data": {
                                    "source": c,
                                    "group": "known" if known else ("slow" if group == "slow" else "candidate"),
                                    "mjd": bd.get("mjd"),
                                    "field": bd.get("field"),
                                },
                                "scans": [scans["field"], k["browndwarfs"]],
                            }
                        )
            rd = seen(Path(store_dir) / "red" / f"{_red_key(row['key'])}.json")
            if rd:
                for c in rd.get("red", []):
                    cra, cdec = _f(c.get("ra")), _f(c.get("dec"))
                    if cra is None or cdec is None:
                        continue
                    add(
                        {
                            "id": find_id("red", cra, cdec),
                            "kind": "red",
                            "place": place,
                            "fov": fov,
                            "ra": cra,
                            "dec": cdec,
                            "rate": None,
                            "snr": _f(c.get("snr_long")),
                            "score": _f(c.get("colour")),
                            "chance": None,
                            "data": {
                                "source": c,
                                "median_colour": rd.get("median_colour"),
                                "bands": rd.get("bands"),
                                "field": rd.get("field"),
                            },
                            "scans": [scans["field"], _red_key(row["key"])],
                        }
                    )
            an = seen(Path(store_dir) / "anomalies" / f"{k['anomalies']}.json")
            if an:
                for a in an.get("anomalies", []):
                    ara, adec = _f(a.get("ra")), _f(a.get("dec"))
                    if ara is None or adec is None:
                        continue
                    add(
                        {
                            "id": find_id("anomaly", ara, adec),
                            "kind": "anomaly",
                            "place": place,
                            "fov": fov,
                            "ra": ara,
                            "dec": adec,
                            "rate": None,
                            "snr": _f(a.get("snr")),
                            "score": _f(a.get("score")),
                            "chance": None,
                            "data": {
                                "anomaly": a,
                                "threshold": an.get("threshold"),
                                "edges": an.get("edges"),
                                "median": an.get("median"),
                                "field": an.get("field"),
                            },
                            "scans": [scans["field"], k["anomalies"]],
                        }
                    )
        elif row["kind"] == "filmsearch" and row["complete"]:
            meta = seen(Path(store_dir) / "products" / "filmsearch" / VERSIONS["filmsearch"] / row["key"][:2] / row["key"] / "meta.json")
            if not meta:
                continue
            g = meta.get("grid") or {}
            fr = meta.get("frames") or []
            place = place_name(targets, float(p["ra"]), float(p["dec"]))
            ctl = meta.get("control") or {}
            for i, t in enumerate(meta.get("tracks", [])):
                pts = t.get("pts") or []
                if not pts or not g:
                    continue
                # every stored track already passed the film search's own bar; an orbit track (verified) is the strong kind, a line track the weaker
                line = t.get("method") == "line" or not t.get("verified")
                ctl_key = "line_chance" if line else "chance"
                per_search = (ctl.get(ctl_key) or {}).get(str(t.get("n")), 0.0)  # a missing key: none seen in the control trials
                mid = pts[len(pts) // 2]
                n, step = g["n"], g["step_arcsec"] / 3600.0
                dec = g["dec0"] + (n / 2 - mid["y"]) * step
                ra = g["ra0"] - (mid["x"] - n / 2) * step / math.cos(math.radians(g["dec0"]))
                mjd = fr[mid["f"]]["mjd"] if mid["f"] < len(fr) else None
                visits = [
                    {
                        "ra": g["ra0"] - (q["x"] - n / 2) * step / math.cos(math.radians(g["dec0"])),
                        "dec": g["dec0"] + (n / 2 - q["y"]) * step,
                        "mjd": fr[q["f"]]["mjd"],
                    }
                    for q in pts
                    if q["f"] < len(fr)
                ]
                add(
                    {
                        "id": find_id("film", ra, dec, str(i)),
                        "kind": "film",
                        "place": place,
                        "fov": float(p.get("fov", 60)),
                        "ra": ra,
                        "dec": dec,
                        "rate": _f(t.get("rate")),
                        "snr": _f(t.get("z_together")),
                        "score": _f(t.get("z_together")),
                        "chance": _f(per_search),
                        "data": {
                            "track": t,
                            "mjd": mjd,
                            "visits": visits,
                            "film": p.get("mjd"),
                            # the lab opens the film's own field (warm, pinned), not a new field centred on the track (M1: a cold build)
                            "field_params": {"ra": float(p["ra"]), "dec": float(p["dec"]), "fov": float(p.get("fov", 60))},
                            "control": ctl,
                            "less_certain": line,
                            "controls_per_search": per_search,
                        },
                        "scans": [row["key"]],
                    }
                )
        elif row["kind"] in ("distant", "deep") and row["complete"]:
            meta = seen(Path(store_dir) / "products" / row["kind"] / VERSIONS[row["kind"]] / row["key"][:2] / row["key"] / "meta.json")
            if not meta:
                continue
            c0 = (p.get("fields") or [[p.get("ra"), p.get("dec")]])[0] if row["kind"] == "deep" else [p.get("ra"), p.get("dec")]
            place = place_name(targets, float(c0[0]), float(c0[1])) if c0 and c0[0] is not None else "?"
            t0 = _f(meta.get("t0")) if row["kind"] == "distant" else None  # the date the one-pass search's places are for
            for c in (q for q in meta.get("candidates", []) if q.get("ok")):
                if t0 is not None and c.get("ref_mjd") is None:
                    c = {**c, "ref_mjd": t0}
                cra, cdec = _f(c.get("ra")), _f(c.get("dec"))
                if cra is None or cdec is None:
                    continue
                add(
                    {
                        "id": find_id("peak", cra, cdec, row["kind"]),
                        "kind": "peak",
                        "place": place,
                        "fov": float(p.get("fov", 18)),
                        "ra": cra,
                        "dec": cdec,
                        "rate": None,
                        "snr": _f(c.get("phot_sn") or c.get("sn_tuned") or c.get("sn")),
                        "score": _f(c.get("sn")),
                        "chance": None,
                        "data": {"peak": c, "search": row["kind"], "control": meta.get("control")},
                        "scans": [row["key"]],
                    }
                )
    sig.update(f"rev{COLLECT_REV}".encode())
    return merge(list(finds.values())), sig.hexdigest()


MERGE_ARCSEC = {"red": 4.0, "anomaly": 4.0, "pair": 6.0, "mover": 6.0, "browndwarf": 6.0, "film": 8.0, "peak": 6.0}


def _sep(a: dict[str, Any], b: dict[str, Any]) -> float:
    c = math.cos(math.radians(a["dec"]))
    return math.hypot((a["ra"] - b["ra"]) * c, a["dec"] - b["dec"]) * 3600.0


def same_object(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The same object seen twice: within the merge distance at the common epoch and, for the ones that move, at the same rate (30 %)."""
    if a["kind"] != b["kind"] or _sep(a, b) > MERGE_ARCSEC.get(a["kind"], 5.0):
        return False
    ra_, rb_ = a.get("rate"), b.get("rate")
    if a["kind"] in ("mover", "browndwarf", "film") and ra_ is not None and rb_ is not None:
        return bool(abs(ra_ - rb_) <= 0.3 * max(ra_, rb_, 0.05))
    return True


def reuse_ids(finds: list[dict[str, Any]], have: list[dict[str, Any]]) -> None:
    """A find already in the table keeps its id (and so its catalogue answers) when the same object comes out of a new sweep a little moved."""
    taken: set[str] = set()
    known = {h["id"] for h in have}
    order = {h["id"]: n for n, h in enumerate(have)}
    taken.update(f["id"] for f in finds if f["id"] in known)
    # only the rows of the same kind within a strip of declination can be the same object: every find against every row held the event
    # loop 1–2 s a sweep (26,000 of each, 23 Nov 2026)
    strip = max(MERGE_ARCSEC.values()) / 3600.0
    near: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for h in have:
        if h["id"] not in taken:
            near.setdefault((h["kind"], math.floor(h["dec"] / strip)), []).append(h)
    for f in finds:
        if f["id"] in taken:
            continue
        b = math.floor(f["dec"] / strip)
        for h in sorted((h for k in (b - 1, b, b + 1) for h in near.get((f["kind"], k), ())), key=lambda h: order[h["id"]]):
            if h["id"] not in taken and same_object(h, f):
                f["id"] = h["id"]
                taken.add(h["id"])
                break


def merge(finds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One find per object: detections of the same object (other field size, other scan, other pass) fold into the one seen in the largest field,
    which lists them all (`data.detections`: place, field size, scans, product version)."""
    out: list[dict[str, Any]] = []
    for f in sorted(finds, key=lambda x: -x.get("fov", 0)):
        src = (f["data"].get("track") or f["data"].get("source") or {}) if isinstance(f["data"], dict) else {}
        det = {
            "place": f["place"],
            "fov": f.get("fov"),
            "scans": f["scans"],
            "versions": f.get("versions"),
            "ra": f["ra"],
            "dec": f["dec"],
            "rate": f.get("rate"),
            "sigma": f.get("score"),
            "rate_err": _f(src.get("speed_err")),
        }
        host = next((o for o in out if same_object(o, f)), None)
        if host is None:
            f["data"]["detections"] = [det]
            out.append(f)
        else:
            host["data"]["detections"].append(det)
            host["scans"] = sorted(set(host["scans"]) | set(f["scans"]))
            for k in ("snr", "score"):
                if (f.get(k) or 0) > (host.get(k) or 0):
                    host[k] = f[k]
    for h in out:
        dets = h["data"]["detections"]
        if h["kind"] in ("mover", "browndwarf") and dets:
            best = max(dets, key=lambda x: x.get("sigma") or 0.0)  # the best measurement of the object, not the one of the biggest field
            if best.get("rate") is not None:
                h["rate"] = best["rate"]
            h["data"]["rate_err"] = best.get("rate_err")
            h["data"]["best_detection"] = {k: best.get(k) for k in ("fov", "rate", "rate_err", "sigma", "place")}
    return out


_PRODUCT = {
    "mover": "movers",
    "pair": "movers",
    "browndwarf": "browndwarfs",
    "red": "red",
    "anomaly": "anomalies",
    "film": "filmsearch",
    "peak": "distant",
}


def extras(f: dict[str, Any]) -> dict[str, Any]:
    """What a find carries beside its summary: its positions per pass, its spectrum, its light curves and the product version that produced it."""
    d, kind = f["data"], f["kind"]
    pos: list[dict[str, Any]] = []
    spec: list[dict[str, Any]] = []
    lc: list[dict[str, Any]] = []
    if kind == "mover":
        t = d.get("track") or {}
        snr = t.get("snr") or []
        for i, (m, ra, dec) in enumerate(zip(t.get("mjd") or [], t.get("ra") or [], t.get("dec") or [], strict=False)):
            pos.append(
                {
                    "label": f"pass {(t.get('passes') or list(range(99)))[i]}",
                    "mjd": m,
                    "ra": ra,
                    "dec": dec,
                    "snr": snr[i] if i < len(snr) else None,
                }
            )
    elif kind == "film":
        t = d.get("track") or {}
        for p in t.get("pts") or []:
            pos.append({"label": f"frame {p.get('f')} ({p.get('how')})", "mjd": None, "ra": None, "dec": None, "snr": p.get("z")})
    elif kind == "anomaly":
        a = d.get("anomaly") or {}
        edges = d.get("edges") or []
        shape, err, model = a.get("shape") or [], a.get("shape_err") or [], a.get("model") or []
        for i, v in enumerate(shape):
            lo = edges[i] if i < len(edges) else None
            hi = edges[i + 1] if i + 1 < len(edges) else None
            spec.append(
                {
                    "lam": lo,
                    "lam_hi": hi,
                    "flux": v,
                    "err": err[i] if i < len(err) else None,
                    "model": model[i] if i < len(model) else None,
                    "src": "binned spectrum, anomaly search",
                }
            )
    elif kind == "pair":
        mjds = d.get("mjd") or []
        for ci, c in enumerate((d.get("pair") or {}).get("components") or []):
            errs = c.get("flux_per_pass_err") or []
            for i, v in enumerate(c.get("flux_per_pass") or []):
                lc.append(
                    {
                        "label": f"component {ci + 1}",
                        "mjd": mjds[i] if i < len(mjds) else None,
                        "flux": v,
                        "err": errs[i] if i < len(errs) else None,
                    }
                )
    elif kind == "peak":
        for i, q in enumerate((d.get("peak") or {}).get("by_pass") or []):
            lc.append({"label": "flux per pass", "mjd": q.get("mjd"), "flux": q.get("flux_mjy"), "err": None})
            pos.append({"label": f"pass {i}", "mjd": q.get("mjd"), "ra": q.get("ra"), "dec": q.get("dec"), "snr": q.get("sn")})
    prod = _PRODUCT.get(kind, kind)
    return {"positions": pos, "spectrum": spec, "lightcurve": lc, "versions": {prod: VERSIONS.get(prod)}}


def _red_key(field_key: str) -> str:
    from service.products.store import product_key

    return product_key("red", {"field": field_key})


class _Maintenance:
    priority = 3  # service.cpu reads it: P3, a background call that gives way to people (service.jobs.scheduler.Priority.P3)


def pending_ids(
    path: str,
    limit: int,
    targets: frozenset[str],
    now: float,
    conn: sqlite3.Connection | None = None,
    front: tuple[str, ...] = (),
    leads: tuple[str, ...] = (),
) -> list[str]:
    """The ids of the finds with a catalogue not yet answered (or that did not answer and may be asked again). The most promising first
    (as Findings orders them), then the kinds that matter most (Find Planet X peaks, movers, brown dwarfs, film tracks, then pairs),
    the test targets' before the rest within each rank, and within that the longest waiting. It reads every find and what each catalogue
    said, so the identification loop runs it in a worker process (service/cpu.py) on its own read-only connection: in the API process it
    took the GIL from the event loop. `leads`: the unexplained tracks (the visible ones first): JPL is asked about the rest of their visits
    before what else waits, after the first page (26 of 28 stood behind 1,100 finds that never run out, 23 Nov 2026)."""
    db = conn or sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    try:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id, kind, place, ra, dec, data, score, snr FROM finds").fetchall()
        done = {
            (r["find_id"], r["catalogue"]): r for r in db.execute("SELECT find_id, catalogue, state, attempts, tried, detail FROM idents")
        }
    finally:
        if conn is None:
            db.close()
    out: list[tuple[tuple[int, int, int, int, int, float], str]] = []
    at_front = {fid: n for n, fid in enumerate(dict.fromkeys((*front, *leads)))}
    is_lead = frozenset(leads)
    for r in rows:
        data = json.loads(r["data"] or "{}")
        want = want_of(r["kind"], r["ra"], r["dec"], data)
        deciding = set(decisive_of(r["kind"], r["ra"] or 0.0, r["dec"] or 90.0, data))
        worst = None
        tier = 1  # 1: only catalogues that add detail are asked again; 0: one that decides, or one never asked (or asked the old way)
        for c in want:
            d = done.get((r["id"], c))
            detail = (d["detail"] or "") if d is not None else ""
            # asked before neighbours were looked for, or before JPL was asked for its precise second pass: asked once more
            old = (
                d is not None
                and d["state"] != "unavailable"
                and (
                    (r["kind"] in BLEND_KINDS and c == "gaia" and '"blend_r"' not in detail)
                    or (c == "sbident" and '"two_pass"' not in detail)
                )
            )
            # a film track JPL was asked about at three of its visits only: the rest once, after what decides, waiting longer after a failure
            rest = d is not None and r["kind"] == "film" and c == "sbident" and d["state"] == "none" and '"all_visits"' not in detail
            if d is not None and rest and not old:
                if now - d["tried"] >= RETRY_S * (2 ** min(d["attempts"], 5)) or not d["attempts"]:
                    worst = 0.0
                    if r["id"] in is_lead:
                        tier = 0
            elif d is None or old:
                worst = 0.0 if worst is None else min(worst, 0.0)  # never asked, or asked the old way
                tier = 0
            # a deciding one is asked until it answers (the wait grows to 32 RETRY_S); one that adds detail too, but past MAX_ATTEMPTS
            # only every EXTRA_RETRY_S
            elif d["state"] == "unavailable" and (
                ((d["attempts"] < MAX_ATTEMPTS or c in deciding) and now - d["tried"] >= RETRY_S * (2 ** min(d["attempts"], 5)))
                or now - d["tried"] >= EXTRA_RETRY_S
            ):
                worst = d["tried"] if worst is None else min(worst, d["tried"])
                if c in deciding:
                    tier = 0
        if worst is not None:
            # the most promising first across kinds (what the page shows first: a lead corrected before the many below it), then the
            # kind's rank, the test targets', the longest waiting
            promise = promise_of(r["kind"], "unexplained", r["score"] if r["score"] is not None else r["snr"], 1)
            # what changes a verdict (a deciding catalogue, a first or a precise second ask) before what only adds detail: with VizieR
            # down, thousands of finds wait on VSX alone and pushed the top track's JPL check to the 30th place
            # within a tier, the first page of Promising as last served (`front`, in its order) before the rest: promise_of here cannot
            # see who has looked at a find, so equal promises (a track's σ is capped) left page 1's tracks behind dozens of others
            key = (
                tier,
                at_front.get(r["id"], len(at_front)),
                -promise,
                IDENT_RANK.get(r["kind"], 2),
                0 if r["place"] in targets else 1,
                worst,
            )
            out.append((key, r["id"]))
    out.sort(key=lambda x: x[0])
    return [i for _, i in out[:limit]]


class FindingsDB:
    """findings.db: the finds, what each catalogue said about each, and the signature of the result files last read."""

    def __init__(self, path: Path, writer: bool) -> None:
        self.path, self.writer = path, writer
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=8000")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.read_only = False
        self.version = SCHEMA
        self._kept_rows: dict[str, tuple[bytes, bytes]] = {}  # read_base: each find's pack and the signature of the rows it was made of
        self._base: tuple[tuple[int, int], list[Any]] | None = None  # query's read of the tables, kept while the database is unchanged
        self._migrate()

    def changes(self) -> tuple[int, int]:
        """What changes whenever the database does: a commit of another connection (data_version) or a write of this one (total_changes).
        Call it under _READ, as every read of this connection. The writer of this process writes through the same connection without
        that lock: a pragma that comes back empty meanwhile (it did on prod, 20 Nov) counts as a change, never twice the same."""
        try:
            row = self.db.execute("PRAGMA data_version").fetchone()
        except sqlite3.Error:
            row = None
        if row is None:
            self._unknown = getattr(self, "_unknown", 0) - 1
            return self._unknown, self._unknown
        return int(row[0]), int(self.db.total_changes)

    def _migrate(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        # the bright 2MASS star near a far-search peak (Findings.wing_loop): apart from idents, so the identification never asks again
        self.db.execute("CREATE TABLE IF NOT EXISTS wings (find_id TEXT PRIMARY KEY, state TEXT, name TEXT, sep REAL, k REAL, tried REAL)")
        row = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        have = int(row[0]) if row else 0
        if have > SCHEMA:  # written by a newer instance: read what is there, change nothing
            self.read_only, self.version = True, have
            return
        if not self.writer:
            return
        if have < 1:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS finds (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, place TEXT, fov REAL, ra REAL NOT NULL, dec REAL NOT NULL, epoch REAL,
                    rate REAL, snr REAL, score REAL, chance REAL, data TEXT, scans TEXT, created REAL, updated REAL
                );
                CREATE INDEX IF NOT EXISTS finds_kind ON finds(kind);
                CREATE TABLE IF NOT EXISTS idents (
                    find_id TEXT NOT NULL, catalogue TEXT NOT NULL, state TEXT NOT NULL, name TEXT, sep REAL, type TEXT, detail TEXT,
                    tried REAL, attempts INTEGER DEFAULT 0, PRIMARY KEY (find_id, catalogue)
                );
                """
            )
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '1')")
            self.db.commit()
        if have < 2:
            # everything about a find in related tables; the product versions that produced the values; the catalogues' full answers
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(finds)")}
            if "versions" not in cols:
                self.db.execute("ALTER TABLE finds ADD COLUMN versions TEXT")
            icols = {r[1] for r in self.db.execute("PRAGMA table_info(idents)")}
            if "raw" not in icols:
                self.db.execute("ALTER TABLE idents ADD COLUMN raw TEXT")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (find_id TEXT NOT NULL, idx INTEGER NOT NULL, label TEXT, mjd REAL, ra REAL, dec REAL, snr REAL,
                    PRIMARY KEY (find_id, idx));
                CREATE TABLE IF NOT EXISTS spectra (find_id TEXT NOT NULL, idx INTEGER NOT NULL, lam REAL, lam_hi REAL, flux REAL, err REAL, model REAL,
                    detector TEXT, mjd REAL, flag TEXT, src TEXT, PRIMARY KEY (find_id, idx));
                CREATE TABLE IF NOT EXISTS lightcurves (find_id TEXT NOT NULL, idx INTEGER NOT NULL, label TEXT, mjd REAL, flux REAL, err REAL,
                    PRIMARY KEY (find_id, label, idx));
                CREATE TABLE IF NOT EXISTS images (find_id TEXT NOT NULL, kind TEXT NOT NULL, pass INTEGER NOT NULL DEFAULT 0, path TEXT, sha1 TEXT,
                    created REAL, PRIMARY KEY (find_id, kind, pass));
                """
            )
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '2')")
            self.db.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()

    def write_finds(self, finds: list[dict[str, Any]]) -> tuple[int, int]:
        """Brings the table to this list: new finds in, changed ones updated (their identification kept), vanished ones out."""
        now = time.time()
        have = {r["id"] for r in self.db.execute("SELECT id FROM finds")}
        keep = set()
        for f in finds:
            keep.add(f["id"])
            self.db.execute(
                "INSERT INTO finds (id, kind, place, fov, ra, dec, epoch, rate, snr, score, chance, data, scans, created, updated, versions) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET versions=excluded.versions,  place=excluded.place, fov=excluded.fov, ra=excluded.ra, dec=excluded.dec, "
                "rate=excluded.rate, snr=excluded.snr, score=excluded.score, chance=excluded.chance, data=excluded.data, scans=excluded.scans, updated=excluded.updated",
                (
                    f["id"], f["kind"], f["place"], f["fov"], f["ra"], f["dec"], EPOCH, f["rate"], f["snr"], f["score"], f["chance"],
                    json.dumps(f["data"], separators=(",", ":")), json.dumps(f["scans"]), now, now, json.dumps(f.get("versions") or {}),
                ),
            )  # fmt: skip
            self._write_related(f)
        gone = have - keep
        for i in gone:
            self.db.execute("DELETE FROM finds WHERE id=?", (i,))
            self.db.execute("DELETE FROM idents WHERE find_id=?", (i,))
        self.db.commit()
        return len(keep - have), len(gone)

    def _write_related(self, f: dict[str, Any]) -> None:
        """The find's positions, spectrum and light curves: replaced, never duplicated, when its scans were computed again."""
        i = f["id"]
        for t in ("positions", "spectra", "lightcurves"):
            self.db.execute(f"DELETE FROM {t} WHERE find_id=?", (i,))
        self.db.executemany(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
            [(i, k, p.get("label"), p.get("mjd"), p.get("ra"), p.get("dec"), p.get("snr")) for k, p in enumerate(f.get("positions") or [])],
        )
        self.db.executemany(
            "INSERT INTO spectra VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    i,
                    k,
                    p.get("lam"),
                    p.get("lam_hi"),
                    p.get("flux"),
                    p.get("err"),
                    p.get("model"),
                    p.get("detector"),
                    p.get("mjd"),
                    p.get("flag"),
                    p.get("src"),
                )
                for k, p in enumerate(f.get("spectrum") or [])
            ],
        )
        self.db.executemany(
            "INSERT INTO lightcurves VALUES (?,?,?,?,?,?)",
            [(i, k, p.get("label"), p.get("mjd"), p.get("flux"), p.get("err")) for k, p in enumerate(f.get("lightcurve") or [])],
        )

    def related(self, ids: list[str]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        out: dict[str, dict[str, list[dict[str, Any]]]] = {
            i: {"positions": [], "spectrum": [], "lightcurve": [], "images": []} for i in ids
        }
        for k in range(0, len(ids), 500):
            chunk = ids[k : k + 500]
            m = ",".join("?" for _ in chunk)
            for r in self.db.execute(f"SELECT * FROM positions WHERE find_id IN ({m}) ORDER BY idx", chunk):
                out[r["find_id"]]["positions"].append({c: r[c] for c in ("label", "mjd", "ra", "dec", "snr")})
            for r in self.db.execute(f"SELECT * FROM spectra WHERE find_id IN ({m}) ORDER BY idx", chunk):
                out[r["find_id"]]["spectrum"].append(
                    {c: r[c] for c in ("lam", "lam_hi", "flux", "err", "model", "detector", "mjd", "flag", "src")}
                )
            for r in self.db.execute(f"SELECT * FROM lightcurves WHERE find_id IN ({m}) ORDER BY label, idx", chunk):
                out[r["find_id"]]["lightcurve"].append({c: r[c] for c in ("label", "mjd", "flux", "err")})
            for r in self.db.execute(f"SELECT * FROM images WHERE find_id IN ({m}) ORDER BY kind, pass", chunk):
                out[r["find_id"]]["images"].append({c: r[c] for c in ("kind", "pass", "path", "sha1")})
        return out

    def pending(self, limit: int, targets: frozenset[str] = frozenset()) -> list[sqlite3.Row]:
        """Finds with a catalogue not yet answered (or that did not answer and may be asked again), in the order pending_ids gives."""
        return self.rows_of(pending_ids(str(self.path), limit, targets, time.time(), conn=self.db))

    def rows_of(self, ids: list[str]) -> list[sqlite3.Row]:
        """The finds' rows (id, kind, place, ra, dec, data) in the order of `ids`."""
        if not ids:
            return []
        m = ",".join("?" for _ in ids)
        got = {r["id"]: r for r in self.db.execute(f"SELECT id, kind, place, ra, dec, data FROM finds WHERE id IN ({m})", ids)}
        return [got[i] for i in ids if i in got]

    def put_ident(
        self,
        find_id: str,
        catalogue: str,
        state: str,
        name: str | None,
        sep: float | None,
        typ: str | None,
        detail: dict[str, Any],
        raw: dict[str, Any] | None = None,
    ) -> None:
        row = self.db.execute("SELECT attempts FROM idents WHERE find_id=? AND catalogue=?", (find_id, catalogue)).fetchone()
        attempts = (int(row[0]) if row else 0) + (1 if state == "unavailable" else 0)
        self.db.execute(
            "INSERT OR REPLACE INTO idents (find_id, catalogue, state, name, sep, type, detail, tried, attempts, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                find_id,
                catalogue,
                state,
                name,
                sep,
                typ,
                json.dumps(detail, separators=(",", ":")),
                time.time(),
                attempts,
                json.dumps(raw, separators=(",", ":")) if raw is not None else None,
            ),
        )

    def put_wing(self, find_id: str, state: str, name: str | None = None, sep: float | None = None, k: float | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO wings (find_id, state, name, sep, k, tried) VALUES (?,?,?,?,?,?)",
            (find_id, state, name, sep, k, time.time()),
        )

    def wings(self) -> dict[str, dict[str, Any]]:
        """The far-search peaks a bright 2MASS star stands near ({id: {name, sep, k}}): only those answered "ok". Call it under _READ."""
        try:
            rows = self.db.execute("SELECT find_id, name, sep, k FROM wings WHERE state='ok'").fetchall()
        except sqlite3.OperationalError:  # a database without the table yet
            return {}
        return {r[0]: {"name": r[1], "sep": r[2], "k": r[3]} for r in rows}

    def wings_to_ask(self, now: float, limit: int) -> list[sqlite3.Row]:
        """The far-search peaks never asked about a bright 2MASS star, or not answered for WING_RETRY_S."""
        return self.db.execute(
            "SELECT f.id, f.ra, f.dec FROM finds f LEFT JOIN wings w ON w.find_id = f.id WHERE f.kind='peak' "
            "AND json_extract(f.data, '$.search')='distant' AND (w.find_id IS NULL OR (w.state='unavailable' AND w.tried < ?)) "
            "ORDER BY f.score DESC LIMIT ?",
            (now - WING_RETRY_S, limit),
        ).fetchall()

    def ident_rows_of(self, ids: list[str]) -> dict[str, list[sqlite3.Row]]:
        """The catalogues' answers of these finds as the table holds them (their JSON not read)."""
        out: dict[str, list[sqlite3.Row]] = {i: [] for i in ids}
        for k in range(0, len(ids), 500):
            chunk = ids[k : k + 500]
            marks = ",".join("?" for _ in chunk)
            for r in self.db.execute(f"SELECT * FROM idents WHERE find_id IN ({marks})", chunk):
                out[r["find_id"]].append(r)
        return out

    @staticmethod
    def ident_of(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "catalogue": r["catalogue"],
            "label": CATALOGUE_LABEL.get(r["catalogue"], r["catalogue"]),
            "state": r["state"],
            "name": r["name"],
            "sep": r["sep"],
            "type": r["type"],
            "detail": json.loads(r["detail"] or "{}"),
            "raw": json.loads(r["raw"]) if r["raw"] else None,
            "tried": r["tried"],
            "attempts": r["attempts"],
        }

    def idents_of(self, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {i: [self.ident_of(r) for r in rows] for i, rows in self.ident_rows_of(ids).items()}


def _ident_from(cat: str, ans: dict[str, Any]) -> tuple[str, str | None, float | None, str | None, dict[str, Any]]:
    """(state, name, separation, type, detail) of one catalogue's answer from `CatalogueContext.context`."""
    st = ans.get("state")
    if st in ("unavailable", "pending") or st is None:
        return "unavailable", None, None, None, {"error": ans.get("error")}
    if st == "none":
        return "none", None, None, None, {}
    if cat == "simbad":
        return "ok", ans.get("name"), ans.get("sep"), ans.get("otype"), {"sptype": ans.get("sptype"), "link": ans.get("link")}
    if cat == "gaia":
        gid = ans.get("source_id")
        return "ok", f"Gaia DR3 {gid}", ans.get("sep"), None, {k: ans.get(k) for k in ("g", "bp_rp", "parallax", "pmra", "pmdec", "ruwe")}
    if cat in ("twomass", "allwise"):
        return (
            "ok",
            ans.get("designation"),
            ans.get("sep"),
            None,
            {k: v for k, v in ans.items() if k not in ("state", "designation", "sep")},
        )
    return (
        "ok",
        ans.get("name"),
        ans.get("sep"),
        ans.get("type"),
        {k: v for k, v in ans.items() if k not in ("state", "name", "type", "sep")},
    )


class Findings:
    leads: tuple[str, ...] = ()  # the unexplained tracks, as the last judged finds say (set_leads)

    def __init__(
        self, store: Store, store_dir: Path, catalogue: Any, movers: Any, pairs: Any, dwarfs: Any, anomalies: Any, writer: bool = True
    ) -> None:
        self.store, self.store_dir, self.catalogue = store, store_dir, catalogue
        self.movers, self.pairs, self.dwarfs, self.anomalies = movers, pairs, dwarfs, anomalies
        self.db = FindingsDB(store_dir / "findings.db", writer)
        self.writer = writer and not self.db.read_only
        # JPL's field search is slow (20-60 s) and suffers from many at once: one request at a time, and a patient breaker
        self.breaker = Breaker("JPL Small-Body Identification", consecutive=6, open_s=60.0)
        self.sb_gate = asyncio.Semaphore(1)
        self.tasks: list[asyncio.Task[None]] = []
        self.lock = asyncio.Lock()
        self.kick = asyncio.Event()
        # the ids of the first page of Promising as last served (service/routes_findings.py): what a visitor sees first is identified
        # first, in that order
        self.front: tuple[str, ...] = self._front_saved()
        # one find's JPL asks at a time (its visits one after another): interleaved with three other finds' asks on JPL's one lane,
        # each answer came after about 14 minutes, and a restart in between lost them all
        self.visits_gate = asyncio.Semaphore(1)
        try:
            self.targets = [
                {"n": o["n"], "ra": o["ra"], "dec": o["dec"]}
                for o in json.loads((Path(__file__).resolve().parent.parent / "data" / "catalog.json").read_text())["catalog"]
                if o.get("b")
            ]
        except (OSError, ValueError, KeyError):
            self.targets = []

    def _front_path(self) -> Path:
        return Path(self.store_dir) / "findings-front.json"

    def _front_saved(self) -> tuple[str, ...]:
        """The first page of Promising as last served, kept across restarts (the page's order: who has looked counts in it)."""
        try:
            got = json.loads(self._front_path().read_text())
            return tuple(str(x) for x in got)[:24] if isinstance(got, list) else ()
        except (OSError, ValueError, AttributeError, TypeError):
            return ()

    def set_front(self, ids: tuple[str, ...]) -> None:
        """The first page of Promising, as the page served it: identified first, and kept for the next start."""
        if ids == self.front:
            return
        self.front = ids
        with contextlib.suppress(OSError):
            atomic_write_bytes(self._front_path(), json.dumps(list(ids)).encode())

    def set_leads(self, snap: Snapshot) -> None:
        """The unexplained tracks as they are judged now, the visible ones first: JPL is asked about the rest of their visits first."""
        got = [f for f in snap.items if f["kind"] == "film" and f["status"] == "unexplained"]
        got.sort(key=lambda f: (f["visible"] is False, -f["promise"], -(f["score"] or 0.0), f["id"]))
        self.leads = tuple(f["id"] for f in got)

    def _front_now(self) -> tuple[str, ...]:
        """The first 24 of Promising as the page orders them (without who has looked: the page's own list replaces it when served)."""
        body = query(self.db, None, "all", None, 24, 0, sort="promise", kinds=PROMISING_KINDS)
        return tuple(x["id"] for x in body["finds"])

    def cleanup(self) -> int:
        """Idempotent, at start: peaks the search itself did not mark ok (kept by an older build) leave the table, with their catalogue rows."""
        gone = []
        for r in self.db.db.execute("SELECT id, data FROM finds WHERE kind='peak'").fetchall():
            if not (json.loads(r["data"] or "{}").get("peak") or {}).get("ok", True):
                gone.append(r["id"])
        for i in gone:
            for t in ("finds", "idents", "positions", "spectra", "lightcurves", "images"):
                self.db.db.execute(f"DELETE FROM {t} WHERE {'id' if t == 'finds' else 'find_id'}=?", (i,))
        self.db.db.commit()
        return len(gone)

    def start(self) -> None:
        if self.writer:
            with contextlib.suppress(Exception):
                self.cleanup()
            self.tasks = [
                asyncio.create_task(self.sweep_loop()), asyncio.create_task(self.identify_loop()), asyncio.create_task(self.wing_loop())
            ]  # fmt: skip

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()

    def notify(self) -> None:
        """A scan finished (hook for the scan workers): sweep now instead of at the next tick."""
        self.kick.set()

    async def sweep(self) -> dict[str, int]:
        rows = await asyncio.to_thread(self.store.listing, ("field", "filmsearch", "distant", "deep"))
        fkeys = {
            r["key"]: {
                "movers": self.movers.key_of(r["key"]),
                "pairs": self.pairs.key_of(r["key"]),
                "browndwarfs": self.dwarfs.key_of(r["key"]),
                "anomalies": self.anomalies.key_of(r["key"]),
            }
            for r in rows
            if r["kind"] == "field"
        }
        # every result file read and parsed: a worker process (in a thread it took the GIL from the event loop for a second, more as the
        # sweep adds results)
        finds, sig = await cpu.run(collect, self.store_dir, rows, fkeys, self.targets)
        if sig == self.db.get_meta("sig"):
            return {"added": 0, "removed": 0, "finds": len(finds)}
        async with self.lock:
            have = await asyncio.to_thread(lambda: [dict(r) for r in self.db.db.execute("SELECT id, kind, ra, dec, rate FROM finds")])
            await asyncio.to_thread(reuse_ids, finds, have)
            added, removed = await asyncio.to_thread(self.db.write_finds, finds)
            self.db.set_meta("sig", sig)
        return {"added": added, "removed": removed, "finds": len(finds)}

    async def sweep_loop(self) -> None:
        cpu.JOB.set(_Maintenance())  # its compute calls (collect) are background ones, never a person's
        while True:
            with contextlib.suppress(Exception):
                await self.sweep()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.kick.wait(), SWEEP_S)
            self.kick.clear()

    async def sbident(
        self,
        ra: float,
        dec: float,
        mjd: float,
        rate: float | None,
        pa: float | None,
        check_rate: bool = True,
        max_rate: float | None = None,
    ) -> dict[str, Any]:
        """JPL's Small-Body Identification at the place and time of a moving find (the position at that very time). A body counts only if it
        stands within 15″, is brighter than V 21, and moves at the find's rate (25 %) and direction (30°); the nearest such body is named with
        both rates. When bodies stand near but none moves like the find, the answer is `none` and says what the nearest was."""
        half = 60.0 / 3600.0
        if getattr(self, "sb_gate", None) is None:
            self.sb_gate = asyncio.Semaphore(1)
        async with self.sb_gate:
            return await self._sbident_ask(ra, dec, mjd, rate, pa, check_rate, half, max_rate)

    async def _sbident_ask(
        self,
        ra: float,
        dec: float,
        mjd: float,
        rate: float | None,
        pa: float | None,
        check_rate: bool,
        half: float,
        max_rate: float | None = None,
    ) -> dict[str, Any]:
        self.breaker.before()
        try:
            r = await jpl.turn(
                lambda: self.catalogue.client.get(
                    SB_IDENT,
                    params={
                        "mpc-code": "500",
                        "obs-time": _iso(mjd),
                        "fov-ra-center": _sexa_ra(ra),
                        "fov-dec-center": _sexa_dec(dec),
                        "fov-ra-hwidth": f"{half / max(0.05, math.cos(math.radians(dec))):.5f}",
                        "fov-dec-hwidth": f"{half:.5f}",
                        "vmag-lim": str(SB_VMAG),
                        # the second pass: positions integrated precisely. The first pass is a quick approximation that can be tens of
                        # arcseconds off: 12304 (1991 SR1) stood 17.7″ from the top time-lapse lead there and 4.4″ in the second pass
                        "two-pass": "true",
                        "suppress-first-pass": "true",
                        # a moving find: comets and interstellar bodies too. JPL leaves out every body without an absolute magnitude by
                        # default (3I/ATLAS, C/2025 A6): film:a5b911ea77dc stood on 3I at all seven visits and read "no known body"
                        **({"mag-required": "false"} if check_rate else {}),
                    },
                    timeout=httpx.Timeout(
                        SB_TIMEOUT_S, connect=10.0
                    ),  # JPL's search of a field often takes 20-60 s, well past the catalogues' 12 s
                ),
                lane="sbident",
            )
            r.raise_for_status()
            body: dict[str, Any] = r.json()
        except Exception:
            self.breaker.failure()
            raise
        self.breaker.success()
        from service.known_field import parse_sexa

        fields = body.get("fields_second") or body.get("fields_first") or body.get("fields") or []
        data = body.get("data_second_pass") or body.get("data_first_pass") or body.get("data") or []
        idx = {n: i for i, n in enumerate(fields)}
        c_ra, c_dec = 'Dist. from center RA (")', 'Dist. from center Dec (")'
        a_ra, a_dec = "Astrometric RA (hh:mm:ss)", "Astrometric Dec (dd mm'ss\")"
        r_ra, r_dec, c_v = 'RA rate ("/h)', 'Dec rate ("/h)', "Visual magnitude (V)"
        near: list[tuple[float, list[Any]]] = []
        for row in data:
            try:
                if a_ra in idx and a_dec in idx:  # the second pass: the body's own place, measured from ours
                    bra, bdec = parse_sexa(str(row[idx[a_ra]]), True), parse_sexa(str(row[idx[a_dec]]), False)
                    if bra is None or bdec is None:
                        continue
                    d_ra = ((bra - ra + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec))
                    near.append((math.hypot(d_ra, bdec - dec) * 3600.0, row))
                else:
                    near.append((math.hypot(float(row[idx[c_ra]]), float(row[idx[c_dec]])), row))
            except (KeyError, ValueError, IndexError):
                continue
        near.sort(key=lambda x: x[0])
        out: dict[str, Any] = {"n_bodies": len(data), "vmag_limit": SB_VMAG, "two_pass": True, "near": None}
        for sep, row in near:  # the nearest body of a known brightness, whatever its motion: what a chained track passed through
            v = _f(row[idx[c_v]]) if c_v in idx else None
            if sep <= SB_MATCH_ARCSEC and v is not None and v <= SB_VMAG:
                out["near"] = {"name": str(row[0]).strip(), "sep": round(sep, 1), "v": v}
                break
        for sep, row in near:
            if sep > SB_MATCH_ARCSEC:
                break
            try:
                vr, vd = float(row[idx[r_ra]]), float(row[idx[r_dec]])
            except (KeyError, ValueError, IndexError):
                out.update(state="none", note=f"{row[0]} stands {sep:.1f}″ away but JPL gave no rate to compare")
                continue
            speed, ang = math.hypot(vr, vd), math.degrees(math.atan2(vr, vd)) % 360.0
            if max_rate is not None and speed > max_rate:
                out["note"] = f"{str(row[0]).strip()} stands {sep:.1f}″ away but moves {speed:.0f}″/h, too fast for this find"
                continue
            agrees = (not check_rate) or (
                rate is not None
                and pa is not None
                and abs(speed - rate) <= SB_RATE_TOL * rate
                and abs((ang - pa + 180.0) % 360.0 - 180.0) <= SB_PA_TOL
            )
            cand = {
                "name": str(row[0]),
                "sep": round(sep, 1),
                "v": row[idx[c_v]] if c_v in idx else None,
                "body_rate": round(speed, 1),
                "body_pa": round(ang),
                "our_rate": rate,
                "our_pa": pa,
            }
            if agrees:
                return {"state": "ok", **out, **cand}
            out.update(
                state="none",
                nearest_rejected=cand,
                note=f"{cand['name']} stands {sep:.1f}″ away but moves {speed:.0f}″/h toward {ang:.0f}°, not like this find",
            )
        out.setdefault("state", "none")
        return out

    def _mjd_of(self, row: sqlite3.Row) -> float | None:
        d = json.loads(row["data"])
        m = d.get("mjd")
        if isinstance(m, list):
            m = m[len(m) // 2] if m else None
        if m is None:
            m = (d.get("track") or {}).get("mjd")
            if isinstance(m, list):
                m = m[len(m) // 2] if m else None
        if m is None:
            m = d.get("film")
        return _fnum(m)

    async def sbident_visits(self, row: sqlite3.Row) -> dict[str, Any]:
        """JPL at the dates of the find's visits. A film track: _film_visits; a brown dwarf candidate near the ecliptic: the same body within 15″ at every pass date."""
        d = json.loads(row["data"])
        if row["kind"] == "film":
            return await self._film_visits(row["id"], d)
        slow = None
        if row["kind"] == "peak":
            slow = PEAK_RATE_MAX  # a search at 30-2000 au does not find a main-belt body that happens to pass there
            ref = _ref_mjd("peak", d)
            sel = [{"ra": row["ra"], "dec": row["dec"], "mjd": ref}] if ref is not None else []
            rate, pa, check, need = None, None, False, 1
        else:
            sel = [{"ra": row["ra"], "dec": row["dec"], "mjd": m} for m in (d.get("mjd") or []) if m is not None]
            rate, pa, check, need = None, None, False, len(sel)
        if not sel:
            return {"state": "unavailable", "error": "no visit dates"}
        got = [await self.sbident(v["ra"], v["dec"], v["mjd"], rate, pa, check, slow) for v in sel]
        names = [g["name"] for g in got if g.get("state") == "ok"]
        best = max(set(names), key=names.count) if names else None
        per = [
            {"mjd": v["mjd"], "state": g["state"], "name": g.get("name"), "sep": g.get("sep"), "note": g.get("note")}
            for v, g in zip(sel, got, strict=True)
        ]
        if best is not None and names.count(best) >= need:
            hit = next(g for g in got if g.get("name") == best)
            return {**hit, "visits_checked": len(sel), "visits_matched": names.count(best), "per_visit": per, "two_pass": True}
        return {
            "state": "none",
            "two_pass": True,
            "visits_checked": len(sel),
            "visits_matched": names.count(best) if best else 0,
            "per_visit": per,
            "n_bodies": max(g.get("n_bodies", 0) for g in got),
        }

    async def _film_visits(self, find_id: str, d: dict[str, Any]) -> dict[str, Any]:
        """A film track against JPL. First, middle and last visit: the same body at two of three, moving like the track, names it. If none
        does, every other visit too (a track chained through one or two places of a known body shows there: chain_of), and a body that
        moves like it at two thirds of all the visits names it. What an earlier check of three visits said is kept, not asked again,
        unless a visit asked now shows a body that moves like the track."""
        vis = list(d.get("visits") or [])
        trk = d.get("track") or {}
        rate, pa = _fnum(trk.get("rate")), _fnum(trk.get("pa"))
        if not vis:
            return {"state": "unavailable", "error": "no visit dates"}
        first = sorted({0, len(vis) // 2, len(vis) - 1})
        memo: dict[str, dict[int, dict[str, Any]]] = self.__dict__.setdefault("_visits_said", {})
        got = memo.setdefault(find_id, {})  # what JPL said so far: a failure half way does not ask again what it answered
        while len(memo) > 64:
            memo.pop(next(iter(memo)))

        async def ask(ks: list[int]) -> None:
            for k in ks:
                if k not in got:
                    v = vis[k]
                    got[k] = await self.sbident(v["ra"], v["dec"], v["mjd"], rate, pa, True)

        def best_of() -> tuple[str | None, int]:
            names = [str(g["name"]) for g in got.values() if g.get("state") == "ok" and g.get("name")]
            best = max(sorted(set(names)), key=names.count) if names else None
            return best, names.count(best) if best is not None else 0

        kept = self._kept_visits(find_id, vis, first)
        if kept is None:
            await ask(first)
        else:
            got.update({k: v for k, v in kept.items() if k not in got})
        best, n = best_of()
        if best is None or n < max(1, math.ceil(len(first) * 2 / 3)):
            await ask(list(range(len(vis))))
            if kept is not None and any(g.get("state") == "ok" for k, g in got.items() if k not in kept):
                for k in kept:
                    if "note" in got[k]:  # (the kept form: asked before comets were, or with fewer visits)
                        del got[k]
                await ask(first)
            best, n = best_of()
            if n < max(1, math.ceil(len(vis) * 2 / 3)):
                best = None
        memo.pop(find_id, None)
        per = [
            {
                "mjd": vis[k]["mjd"],
                "state": g["state"],
                **{c: g.get(c) for c in ("name", "sep", "note")},
                **({"near": g["near"]} if "near" in g else {}),  # (an earlier answer, kept, does not say)
            }
            for k, g in sorted(got.items())
        ]
        said = {"two_pass": True, "all_visits": True, "visits_checked": len(got), "visits_matched": n, "per_visit": per}
        if best is not None:
            return {**next(g for _, g in sorted(got.items()) if g.get("name") == best), **said}
        return {"state": "none", **said, "n_bodies": max(int(g.get("n_bodies") or 0) for g in got.values())}

    def _kept_visits(self, find_id: str, vis: list[dict[str, Any]], first: list[int]) -> dict[int, dict[str, Any]] | None:
        """What the check of three visits (before all of them were asked) said of each: {visit: answer}, or None when there is none."""
        with _READ:
            r = self.db.db.execute("SELECT state, detail FROM idents WHERE find_id=? AND catalogue='sbident'", (find_id,)).fetchone()
        if r is None or r["state"] != "none":
            return None
        det = json.loads(r["detail"] or "{}")
        per = {v.get("mjd"): v for v in det.get("per_visit") or []}
        if not det.get("two_pass") or any(vis[k]["mjd"] not in per for k in first):
            return None
        return {k: {**per[vis[k]["mjd"]], "n_bodies": det.get("n_bodies", 0)} for k in first}

    async def bright_neighbour(self, ra: float, dec: float) -> dict[str, Any]:
        """The brightest Gaia DR3 star (G < 10) that stands within 60″ of the place at the epoch (each moved from 2016 with its proper motion)."""
        from service.context import GAIA_EPOCH, GAIA_TAPS, gaia_rows, moved, sep_arcsec

        cone = (HALO_ARCSEC + 250.0) / 3600.0  # wide: a fast bright star (Barnard's) has moved up to ~100″ since 2016
        err: Exception | None = None
        for url, table, _label in GAIA_TAPS:
            adql = (
                f"SELECT TOP 40 source_id, ra, dec, pmra, pmdec, phot_g_mean_mag FROM {table} "
                f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {cone:.6f})) "
                f"AND phot_g_mean_mag < {HALO_MAG} ORDER BY phot_g_mean_mag"
            )
            try:
                body = await self.catalogue._tap("gaia", url, adql)
            except (httpx.HTTPError, BreakerOpenError) as e:
                err = e
                continue
            best: tuple[float, dict[str, Any]] | None = None
            for r in gaia_rows(body):
                try:
                    a, d = moved(
                        float(r["ra"]), float(r["dec"]), float(r.get("pmra") or 0.0), float(r.get("pmdec") or 0.0), EPOCH - GAIA_EPOCH
                    )
                    mag = float(r["phot_g_mean_mag"])
                except (KeyError, ValueError):
                    continue
                sep = sep_arcsec(a, d, ra, dec)
                if sep <= HALO_ARCSEC and (best is None or mag < best[1]["g"]):
                    best = (sep, {"state": "ok", "name": f"Gaia DR3 {r['source_id']} (G {mag:.1f})", "sep": round(sep, 1), "g": mag})
            return best[1] if best else {"state": "none"}
        raise err or RuntimeError("no Gaia archive")

    async def bright_ir_star(self, ra: float, dec: float) -> dict[str, Any]:
        """The brightest 2MASS star of K <= WING_K WING_MIN_ARCSEC to WING_MAX_ARCSEC from the place: IRSA's TAP, else the 2MASS magnitudes
        of AllWISE's copy in AWS (CDS was down on 26 Nov). A bright star barely moves between 2MASS and now: no motion is applied."""
        from service.context import IRSA_TAP, allwise_cone, sep_arcsec

        cone = (WING_MAX_ARCSEC + 1.0) / 3600.0
        adql = (
            "SELECT TOP 20 designation, ra, dec, k_m FROM fp_psc WHERE "
            f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {cone:.6f})) = 1 AND k_m <= {WING_K}"
        )
        rows: list[dict[str, Any]] = []
        via = "irsa"
        try:
            body = await self.catalogue._tap("irsa", IRSA_TAP, adql)
            head, *lines = [ln for ln in body.splitlines() if ln.strip()]
            cols = [c.strip().strip('"') for c in head.split(",")]
            if "k_m" not in cols:
                raise ValueError("IRSA answered no table")
            for ln in lines:
                v = dict(zip(cols, ln.split(","), strict=False))
                rows.append(
                    {"name": f"2MASS J{v['designation'].strip()}", "ra": float(v["ra"]), "dec": float(v["dec"]), "k": float(v["k_m"])}
                )
        except (httpx.HTTPError, BreakerOpenError, ValueError, KeyError):
            via = "allwise"
            got = await cpu.run(allwise_cone, ra, dec, WING_MAX_ARCSEC + 1.0)
            rows = [
                {
                    "name": f"2MASS star of AllWISE {r.get('designation')}",
                    "ra": float(r["ra"]),
                    "dec": float(r["dec"]),
                    "k": float(r["k_m_2mass"]),
                }
                for r in got
                if r.get("k_m_2mass") is not None and float(r["k_m_2mass"]) <= WING_K
            ]
        best: dict[str, Any] | None = None
        for r in rows:
            sep = sep_arcsec(r["ra"], r["dec"], ra, dec)
            if WING_MIN_ARCSEC <= sep <= WING_MAX_ARCSEC and (best is None or r["k"] < best["k"]):
                best = {"state": "ok", "name": r["name"], "sep": round(sep, 1), "k": round(r["k"], 2)}
        return {**(best or {"state": "none"}), "via": via}

    async def wing_loop(self) -> None:
        """Every far-search peak asked once whether a bright 2MASS star stands near it (bright_ir_star), WING_AT_ONCE at a time, the
        strongest first; the answer is kept (FindingsDB.wings) and never asked again. Every WING_LOG_S the log says how far it is."""
        cpu.JOB.set(_Maintenance())
        await asyncio.sleep(WING_FIRST_S)
        said = {"asked": 0, "ok": 0, "none": 0, "failed": 0, "allwise": 0}
        last = time.monotonic()
        gate = asyncio.Semaphore(WING_AT_ONCE)

        async def one(row: sqlite3.Row) -> None:
            async with gate:
                try:
                    h = await self.bright_ir_star(float(row["ra"]), float(row["dec"]))
                except Exception:  # neither IRSA nor the copy answered: asked again later
                    h = {"state": "unavailable"}
                said["asked"] += 1
                said["failed" if h["state"] == "unavailable" else h["state"]] += 1
                said["allwise"] += 1 if h.get("via") == "allwise" else 0
                await asyncio.to_thread(self._put_wing, row["id"], h)
                await asyncio.sleep(WING_PAUSE_S)

        while True:
            try:
                todo = await asyncio.to_thread(self._wings_to_ask)
                if todo:
                    await asyncio.gather(*(one(r) for r in todo))
                if time.monotonic() - last >= WING_LOG_S or not todo:
                    left = len(await asyncio.to_thread(self._wings_to_ask))
                    if said["asked"] or left:
                        log.warning(
                            "wings: asked %d (ok %d, none %d, failed %d, via AllWISE %d), left %d",
                            said["asked"], said["ok"], said["none"], said["failed"], said["allwise"], left,
                        )  # fmt: skip
                    last = time.monotonic()
                if not todo:
                    await asyncio.sleep(300)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(60)

    def _wings_to_ask(self) -> list[sqlite3.Row]:
        with _READ:
            return self.db.wings_to_ask(time.time(), 50)

    def _put_wing(self, find_id: str, h: dict[str, Any]) -> None:
        with _READ:
            self.db.put_wing(find_id, h["state"], h.get("name"), h.get("sep"), h.get("k"))
            self.db.db.commit()

    async def identify_one(self, row: sqlite3.Row) -> None:
        want = want_of(row["kind"], row["ra"], row["dec"], json.loads(row["data"] or "{}"))
        if "sbident" in want:
            try:
                gate = getattr(self, "visits_gate", None) or asyncio.Semaphore(1)
                async with gate:
                    a = await self.sbident_visits(row)
                keep = (
                    "v",
                    "n_bodies",
                    "body_rate",
                    "body_pa",
                    "our_rate",
                    "our_pa",
                    "note",
                    "nearest_rejected",
                    "all_visits",
                    "visits_checked",
                    "visits_matched",
                    "per_visit",
                    "two_pass",
                )
                self.db.put_ident(row["id"], "sbident", a["state"], a.get("name"), a.get("sep"), None, {k: a[k] for k in keep if k in a}, a)
            except (httpx.HTTPError, BreakerOpenError, ValueError) as e:
                r = self.db.db.execute("SELECT state FROM idents WHERE find_id=? AND catalogue='sbident'", (row["id"],)).fetchone()
                if r is not None and r["state"] in ("ok", "none"):  # asked again for more: what JPL said before stands, asked later
                    self.db.db.execute(
                        "UPDATE idents SET tried=?, attempts=attempts+1 WHERE find_id=? AND catalogue='sbident'", (time.time(), row["id"])
                    )
                elif not isinstance(e, BreakerOpenError):  # JPL is resting after failures: the find stays pending, no attempt spent
                    self.db.put_ident(row["id"], "sbident", "unavailable", None, None, None, {"error": str(e)[:120]})
        if "halo" in want:
            try:
                h = await self.bright_neighbour(row["ra"], row["dec"])
                self.db.put_ident(row["id"], "halo", h["state"], h.get("name"), h.get("sep"), None, {k: h[k] for k in ("g",) if k in h}, h)
            except (httpx.HTTPError, BreakerOpenError, ValueError, RuntimeError) as e:
                self.db.put_ident(row["id"], "halo", "unavailable", None, None, None, {"error": str(e)[:120]})
        if "simbad" in want:
            blend = row["kind"] in BLEND_KINDS
            ctx = await self.catalogue.context(row["ra"], row["dec"], EPOCH, BLEND_ARCSEC if blend else RADIUS)
            for cat in CATALOGUES:
                a = ctx.get(cat) or {"state": "unavailable", "error": "no answer"}
                state, name, sep, typ, detail = _ident_from(cat, a)
                if blend:
                    detail = {**detail, "blend_r": BLEND_ARCSEC}
                    if state == "ok" and sep is not None and sep > RADIUS:  # not this find: its neighbour in the picture
                        detail = {"blend_r": BLEND_ARCSEC, "neighbour": {"name": name, "sep": sep, "type": typ}}
                        state, name, sep, typ = "none", None, None, None
                self.db.put_ident(row["id"], cat, state, name, sep, typ, detail, a)
        self.db.db.commit()

    async def identify_loop(self) -> None:
        # maintenance: its compute calls (the pending scan) are background ones; outside any job they counted as a person's call, and a
        # person's call every 15 s held the sweep's share of the workers at service.cpu.PERSON_LOW_CAP
        cpu.JOB.set(_Maintenance())
        while True:
            try:
                if not self.front:  # after a restart, until a visitor loads Findings: the first page as the page would order it
                    self.front = await asyncio.to_thread(self._front_now)
                ids = await cpu.run(
                    pending_ids, str(self.db.path), 8, frozenset(t["n"] for t in self.targets), time.time(), None, self.front, self.leads
                )
                batch = await asyncio.to_thread(self.db.rows_of, ids)
                if not batch:
                    await asyncio.sleep(15)
                    continue
                gate = asyncio.Semaphore(4)

                async def one(row: sqlite3.Row, gate: asyncio.Semaphore = gate) -> None:
                    async with gate:
                        try:
                            await self.identify_one(row)
                        except (httpx.HTTPError, BreakerOpenError, ValueError, OSError):
                            await asyncio.sleep(1)

                await asyncio.gather(*(one(r) for r in batch))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(10)


def _fnum(v: Any) -> float | None:
    return _f(v)


# ---- reading -------------------------------------------------------------------------------------------------------------------------------------


def _pm_agrees(f: dict[str, Any], idents: list[dict[str, Any]]) -> bool:
    """A catalogue source within reach whose proper motion (mas/yr) equals the find's rate (″/yr) to 30 %: the same moving star."""
    rate = f.get("rate")
    if rate is None:
        return False
    for i in idents:
        raw = i.get("raw") or {}
        if i["catalogue"] in ("simbad", "gaia") and i["state"] == "ok" and raw.get("pmra") is not None and raw.get("pmdec") is not None:
            sp = math.hypot(float(raw["pmra"]), float(raw["pmdec"])) / 1000.0
            if abs(sp - rate) <= 0.3 * max(rate, 0.5):
                return True
    return False


def _pm_of(i: dict[str, Any]) -> float | None:
    raw = i.get("raw") or {}
    if raw.get("pmra") is None or raw.get("pmdec") is None:
        return None
    return math.hypot(float(raw["pmra"]), float(raw["pmdec"])) / 1000.0


DWARF_PM_RATIO = 2.0  # a catalogue's motion within this factor of ours ...
DWARF_PM_ANGLE = 45.0  # ... and this many degrees of its direction is the same brown dwarf


def _dwarf_moves_like(f: dict[str, Any], i: dict[str, Any]) -> bool:
    """A catalogue source (SIMBAD or Gaia, with its own proper motion) that moves as the brown dwarf's search says it does: within
    DWARF_PM_RATIO in size and DWARF_PM_ANGLE in direction. A source without a motion never does (26 Nov 2026: nine of twelve were
    called known brown dwarfs on a radio source or a still star a few arcseconds off, at 3-11″/yr)."""
    src = (f.get("data") or {}).get("source") or {}
    raw = i.get("raw") or {}
    if i["catalogue"] not in ("simbad", "gaia") or i["state"] != "ok":
        return False
    a, b = (_f(src.get("pmra")), _f(src.get("pmdec"))), (_f(raw.get("pmra")), _f(raw.get("pmdec")))
    if None in a or None in b:
        return False
    sa, sb = math.hypot(*a), math.hypot(*b)  # type: ignore[arg-type]
    if sa <= 0 or sb <= 0 or not (1 / DWARF_PM_RATIO <= sb / sa <= DWARF_PM_RATIO):
        return False
    cos = (a[0] * b[0] + a[1] * b[1]) / (sa * sb)  # type: ignore[operator]
    return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= DWARF_PM_ANGLE


def _motion_identity(f: dict[str, Any], idents: list[dict[str, Any]]) -> None:
    """For a mover or brown dwarf the name is the catalogue source that moves as it does. SIMBAD at the motion-corrected place (the scan's own
    answer, or the stored one) wins over a Gaia neighbour; a Gaia row whose motion does not match is marked a neighbour."""
    if f["kind"] not in ("mover", "browndwarf") or f.get("rate") is None:
        return
    rate = f["rate"]

    def agrees(v: float | None) -> bool:
        return v is not None and abs(v - rate) <= 0.3 * max(rate, 0.5)

    for i in idents:
        if i["catalogue"] == "gaia" and i["state"] == "ok" and _pm_of(i) is not None and not agrees(_pm_of(i)):
            i["note"] = "a neighbour: its proper motion does not match the find"
    if f["kind"] == "browndwarf":  # named only by a catalogue source that moves as it does (_dwarf_moves_like)
        m = next((i for c in ("simbad", "gaia") for i in idents if i["catalogue"] == c and i.get("name") and _dwarf_moves_like(f, i)), None)
        if m is not None:
            f["identity"] = {
                "name": m["name"], "catalogue": "SIMBAD" if m["catalogue"] == "simbad" else "Gaia DR3", "type": m.get("type"),
                "basis": "same proper motion",
            }  # fmt: skip
        return
    sb = next((i for i in idents if i["catalogue"] == "simbad" and i["state"] == "ok" and i.get("name")), None)
    src = (f["data"].get("source") or {}).get("simbad") or {}
    if sb is not None and (agrees(_pm_of(sb)) or (_pm_of(sb) is None and src.get("state") == "ok")):
        f["identity"] = {"name": sb["name"], "catalogue": "SIMBAD", "type": sb.get("type"), "basis": "named at the motion-corrected place"}
    elif src.get("state") == "ok" and src.get("name"):
        f["identity"] = {
            "name": src["name"],
            "catalogue": "SIMBAD",
            "type": src.get("otype"),
            "basis": "named by the scan at the motion-corrected place",
        }
    else:
        g = next((i for i in idents if i["catalogue"] == "gaia" and i["state"] == "ok" and agrees(_pm_of(i))), None)
        if g is not None:
            f["identity"] = {"name": g["name"], "catalogue": "Gaia DR3", "type": None, "basis": "same proper motion"}


def _claimed_motion(f: dict[str, Any]) -> tuple[float | None, float | None]:
    """(″/yr, ″ over the baseline) the search claims for a mover or brown-dwarf candidate."""
    d = f.get("data") or {}
    src, t = d.get("source") or {}, d.get("track") or {}
    pm = src.get("pm")
    rate = float(pm) / 1000.0 if pm is not None else (float(t["speed"]) if t.get("speed") is not None else None)
    shift = src.get("shift_arcsec", t.get("shift_arcsec"))
    if shift is None and rate is not None:
        shift = rate * float(src.get("baseline_years") or t.get("baseline_years") or 1.0)
    return rate, (float(shift) if shift is not None else None)


def blend_of(f: dict[str, Any], idents: list[dict[str, Any]]) -> str | None:
    """Why a mover or brown-dwarf candidate did not really move, or None: a catalogue neighbour within BLEND_ARCSEC and a claimed shift
    within BLEND_ARCSEC too (the centroid moves inside the blend), or an AllWISE / 2MASS source at today's place where the claimed motion would
    have carried it at least STILL_FACTOR x RADIUS away since that survey (it was there then)."""
    rate, shift = _claimed_motion(f)
    for i in idents:
        nb = (i.get("detail") or {}).get("neighbour")
        # both within 1.5 pixels: the neighbour blends with it, and a claimed shift that small (noisy on a short baseline) is the
        # centroid moving inside the blend
        if nb and nb.get("sep") is not None and shift is not None and float(nb["sep"]) <= BLEND_ARCSEC and shift <= BLEND_ARCSEC:
            return f"neighbour:{i.get('label', i['catalogue'])}:{nb.get('name')}:{float(nb['sep']):.1f}"
    for c, epoch in SURVEY_EPOCH.items():
        hit = next((x for x in idents if x["catalogue"] == c), None)
        here = hit is not None and hit["state"] == "ok" and hit.get("sep") is not None and hit["sep"] <= RADIUS
        if hit is not None and here and rate is not None and rate * (EPOCH - epoch) >= STILL_FACTOR * RADIUS:
            return f"still:{hit.get('label', c)}:{hit.get('name')}:{float(hit['sep']):.1f}:{epoch}"
    return None


AGN_W12 = 0.8  # AllWISE W1 - W2 at or over it: the colours of an active galaxy (Stern et al. 2012)
BROWN_W12 = 1.5  # ... and over this, with nothing in 2MASS: also those of a cold brown dwarf


def catalogue_class(f: dict[str, Any], idents: list[dict[str, Any]]) -> dict[str, str] | None:
    """What the catalogues' own numbers say a red source or an odd spectrum most likely is, or None: {label, basis}. SIMBAD's type first;
    else AllWISE's colours (with 2MASS). A class for the reader, never a status: a colour is not an identity."""
    by = {i["catalogue"]: i for i in idents}
    sb = by.get("simbad") or {}
    if sb.get("state") == "ok" and sb.get("type") and str(sb["type"]) not in GENERIC_TYPES:
        return {"label": str(sb["type"]), "basis": f"SIMBAD: {sb.get('name')}"}
    aw = by.get("allwise") or {}
    if aw.get("state") != "ok":
        return None
    d = aw.get("detail") or {}
    w1, w2 = _f(d.get("W1")), _f(d.get("W2"))
    src = (f.get("data") or {}).get("source") or {}
    pm = math.hypot(_f(src.get("pmra")) or 0.0, _f(src.get("pmdec")) or 0.0) / 1000.0 if src.get("moves") else 0.0
    far = pm * (EPOCH - SURVEY_EPOCH["allwise"])
    if far >= STILL_FACTOR * RADIUS and aw.get("sep") is not None and aw["sep"] <= RADIUS:
        return {
            "label": "stood still since 2010",
            "basis": f"AllWISE has it {aw['sep']:.1f}″ from here; moving as measured it would be {far:.0f}″ away",
        }
    if w1 is None or w2 is None:
        return {"label": "an infrared source", "basis": "AllWISE"}
    two = (by.get("twomass") or {}).get("state")
    c12 = w1 - w2
    basis = f"AllWISE W1−W2 = {c12:.2f}"
    if c12 >= BROWN_W12 and two == "none":
        return {"label": "very red: an active galaxy or a cold brown dwarf", "basis": f"{basis}, nothing in 2MASS"}
    if c12 >= AGN_W12:
        return {"label": "colours of an active galaxy", "basis": basis}
    return {"label": "neither an active galaxy's nor a cold brown dwarf's colours", "basis": basis}


def ask_people(f: dict[str, Any], idents: list[dict[str, Any]]) -> bool:
    """Is a red source or an odd spectrum a question for a person? Only once SIMBAD and AllWISE have answered and neither has anything
    there (and 2MASS has nothing): 14,400 of 15,000 stand in AllWISE, whose colours answer better than an eye does."""
    by = {i["catalogue"]: i.get("state") for i in idents}
    return by.get("simbad") == "none" and by.get("allwise") == "none" and by.get("twomass") in ("none", "unavailable")


_BESIDE = re.compile(r"^(.+?) stands ([0-9.]+)″ away but ")  # how a visit's note named a body before `near` was kept


def chain_of(f: dict[str, Any], idents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A film track that passes through a known body and is not that body: one real body chained with noise. {name, n: visits beside it, of:
    visits asked, sep: the nearest}, or None. One visit counts within CHAIN_ARCSEC, two of the same body within SB_MATCH_ARCSEC. (4754
    Panthoos, 5511 Cloanthus and 2017 Wesson led the first page as unexplained tracks, 23 Nov 2026.)"""
    if f["kind"] != "film":
        return None
    sb = next((i for i in idents if i["catalogue"] == "sbident" and i["state"] == "none"), None)
    per = ((sb or {}).get("detail") or {}).get("per_visit") or []
    beside: dict[str, list[float]] = {}
    for v in per:
        near = v.get("near")
        if near is None and v.get("name") and v.get("sep") is not None:
            near = v  # it moves like the track, at too few visits to be the track
        if "near" not in v and near is None and (m := _BESIDE.match(v.get("note") or "")):
            near = {"name": m[1], "sep": m[2]}
        if near is not None and float(near["sep"]) <= SB_MATCH_ARCSEC:
            beside.setdefault(str(near["name"]).strip(), []).append(float(near["sep"]))
    hits = [(len(s), -min(s), n) for n, s in beside.items() if len(s) > 1 or min(s) <= CHAIN_ARCSEC]
    if not hits:
        return None
    n, sep, name = max(hits)
    return {"name": name, "n": n, "of": len(per), "sep": -sep}


def status_of(f: dict[str, Any], idents: list[dict[str, Any]]) -> str:
    """`known` (a catalogue or the scan itself names what it is), `unchecked` (catalogues still to answer or that did not answer, and none explains
    it yet) or `unexplained` (every catalogue answered and none explains it)."""
    by = {i["catalogue"]: i for i in idents}

    def ok(c: str) -> bool:
        return by.get(c, {}).get("state") == "ok"

    d = f["data"]
    kind = f["kind"]
    known = False
    if kind == "peak" and not (d.get("peak") or {}).get("ok", True):
        return "below"  # the search itself did not mark it ok: below its bar, not a lead
    if kind in ("mover", "browndwarf"):
        src = (d.get("source") or {}).get("simbad") or {}
        sb = by.get("simbad", {})
        if (sb.get("state") == "ok" and is_galaxy(sb.get("name"), sb.get("type"))) or (
            src.get("state") == "ok" and is_galaxy(src.get("name"), src.get("otype"))
        ):
            return "galaxy"  # SIMBAD calls it a galaxy: the motion claimed is spurious
    if kind == "mover":
        t = d.get("track") or {}
        known = t.get("check") == "same" or bool(t.get("name")) or str(t.get("status", "")).startswith("known") or _pm_agrees(f, idents)
    elif kind == "browndwarf":
        known = any(_dwarf_moves_like(f, i) for i in idents)  # the scan's own "known" (a name at the place) is not enough
    elif kind == "film":
        known = ok("sbident")
    elif kind in ("red", "anomaly"):
        # SIMBAD must say what it is: a bare "Star" or "IR" explains neither a red colour nor an odd spectrum
        known = ok("simbad") and str(by["simbad"].get("type") or "") not in GENERIC_TYPES
    elif kind == "pair":
        known = ok("vsx")
    if kind == "peak" and ok("sbident"):
        known = True
    if known:
        return "known"
    if kind == "film" and (d.get("track") or {}).get("method") == "line" and not (d.get("track") or {}).get("verified"):
        # linked as a straight line, its light never checked in the exposures (on - off): on 18 Nov 2026 the R Draconis track's four
        # strongest points (39-62 sigma in the motion map) held no light at all in the exposures. Not a lead until it is checked
        return "unverified"
    if kind == "film" and chain_of(f, idents):
        return "chain"  # one known body chained with noise: shown, marked, not a lead
    if kind == "film" and (z := _f((d.get("track") or {}).get("z_together"))) is not None and z < WEAK_Z:
        return "weak"  # kept, behind its own filter: under the signal at which tracks are bodies
    if kind in BLEND_KINDS and blend_of(f, idents):
        return "blend"  # a neighbour star or a survey source at its place says it did not move: not a lead
    if kind == "peak" and ok("halo"):
        return "halo"  # inside the halo of a bright star: its glare, not a body
    if kind == "peak":
        # a peak on a catalogued star is most likely that star's residual in the difference, not a body: said so and kept out of the leads
        for c in ("simbad", "gaia", "twomass", "allwise"):
            i = by.get(c, {})
            if i.get("state") == "ok" and i.get("sep") is not None and i["sep"] <= RESIDUAL_ARCSEC:
                return "residual"
    if kind == "peak" and _ref_mjd(kind, d) is None:
        # without the date its place is for, the peak cannot be compared with the known bodies (JPL): it is not called unexplained
        return "unchecked"
    want = decisive_of(kind, f.get("ra") or 0.0, f.get("dec") or 90.0, d)

    def open_(c: str) -> bool:
        i = by.get(c, {})
        st = i.get("state", "pending")
        # a deciding catalogue that has not answered keeps a find unchecked, however many times it was asked: "unexplained" says the
        # check was made ("JPL lists no known body there"), so it is never said while that check did not answer (it is asked again)
        return st in ("pending", "unavailable")

    if any(open_(c) for c in want):
        return "unchecked"
    return "unexplained"


def verdict_of(f: dict[str, Any], idents: list[dict[str, Any]], status: str) -> str:
    """One plain sentence about what the catalogues say."""
    asked = [i for i in idents if i["state"] != "pending"]
    failed = [i.get("label", i["catalogue"]) for i in asked if i["state"] == "unavailable"]
    named = [
        f"{i.get('label', i['catalogue'])}: {i['name']}"
        + (f" ({i['type']})" if i.get("type") else "")
        + (f", {i['sep']:.1f}″ away" if i.get("sep") is not None else "")
        for i in idents
        if i["state"] == "ok"
    ]
    if status == "set_aside":
        a = f.get("people_aside") or {}
        n = f" ({a['agree']} of {a['total']})" if a.get("agree") and a.get("total") else ""
        return f"People set it aside{n}: they agree it is an artefact (a ghost, a streak or a glow), not a body. Not counted as a lead."
    if status == "halo":
        h = next((i for i in idents if i["catalogue"] == "halo" and i["state"] == "ok"), None)
        near = f" ({h['name']}, {h['sep']:.0f}″ away)" if h else ""
        return f"Near a bright star{near}: inside its halo, so most likely its glare and not a body. Not counted as a lead."
    if status == "below":
        return "The search did not mark this peak ok: it is below its own bar, so it is not a lead. Not counted."
    if status == "galaxy":
        sb = next((i for i in idents if i["catalogue"] == "simbad" and i["state"] == "ok"), None)
        who = f"{sb['name']}" + (f" ({sb['type']})" if sb.get("type") else "") if sb else "a galaxy catalogue"
        src = ((f["data"].get("source") or {}).get("simbad") or {}).get("name")
        return f"Matched to a galaxy ({who if sb else (src or who)}), so the motion claimed is spurious: a galaxy, not a mover or a brown dwarf. Not counted as a find."
    if not idents or all(i["state"] == "pending" for i in idents):
        return "Not yet checked: the catalogues have not been asked yet."
    decisive = decisive_of(f["kind"], f.get("ra") or 0.0, f.get("dec") or 90.0, f.get("data") or {})
    waiting = [i.get("label", i["catalogue"]) for i in idents if i["state"] == "pending" and i["catalogue"] in decisive]
    if status == "unverified":
        return ("Linked as a straight line through the visits, but its light was never checked in the exposures themselves (on − off "
                "photometry, which every orbit track passes): near bright stars such tracks can be built from the motion maps alone. "
                "Not counted as a lead.")  # fmt: skip
    if status == "weak":
        z = _f(((f.get("data") or {}).get("track") or {}).get("z_together")) or 0.0
        return (f"A weak track: its signal, {z:.1f} times the noise, is under {WEAK_Z:.0f}. Of the known bodies this search found, 56 of 60 stand "
                "above that; of the tracks under it, most move like noise linked by chance. Kept, but not counted as a lead.")  # fmt: skip
    if status == "chain":
        c = chain_of(f, idents) or {"name": "a known body", "n": 1, "of": 1, "sep": 0.0}
        return (f"A chain through a known asteroid: {c['name']} stands {c['sep']:.1f}″ from the track at {c['n']} of the {c['of']} visits JPL was "
                "asked about, but does not move like it. Most likely that body linked with noise. Not counted as a lead.")  # fmt: skip
    if status == "blend":
        why = (blend_of(f, idents) or "").split(":")
        if why[0] == "neighbour" and len(why) >= 4:
            return (f"It moves only as far as a neighbour {why[3]}″ away ({why[1]}: {why[2]}): most likely the light of the two blending, "
                    "not a body. Not counted as a lead.")  # fmt: skip
        if why[0] == "still" and len(why) >= 5:
            rate, _ = _claimed_motion(f)
            far = f" {rate * (EPOCH - float(why[4])):.0f}″ away by now" if rate else ""
            return (f"{why[1]} has a source at this place ({why[2]}, {why[3]}″ away) in {float(why[4]):.0f}; moving as claimed it would be"
                    f"{far or ' far from it'}: it stood still. Not counted as a lead.")  # fmt: skip
        return "A blend or a source that stood still, not a body. Not counted as a lead."
    if status == "residual":
        star = next(
            (
                f"{i.get('label', i['catalogue'])}: {i['name']}, {i['sep']:.1f}″ away"
                for i in idents
                if i["state"] == "ok" and i.get("sep") is not None and i["sep"] <= RESIDUAL_ARCSEC
            ),
            "a catalogued star",
        )
        return f"It sits on a catalogued star ({star}): most likely a residual of that star in the difference, not a body. Not counted as a lead."
    if status == "unchecked" and f["kind"] == "peak" and _ref_mjd("peak", f.get("data") or {}) is None:
        # the catalogues may all have answered: what is missing is the date, without which JPL cannot be asked about a known body
        text = ("Not yet checked: the date this peak's place is for is not recorded (it was found before the search saved it), so JPL "
                "cannot be asked whether a known body was there.")  # fmt: skip
        if failed:
            text += f" Did not answer: {', '.join(failed)}."
        return text + (f" Named so far: {'; '.join(named[:2])}." if named else "")
    if status == "unchecked":
        text = "Not yet checked: the catalogues that decide have not all answered."
        if waiting:
            text += f" Still to answer: {', '.join(waiting)}."
        if failed:
            text += f" Did not answer: {', '.join(failed)}."
        return text + (f" Named so far: {'; '.join(named[:2])}." if named else "")
    kind = f["kind"]
    if status == "known":
        ident = f.get("identity")
        who = "; ".join(named[:2]) if named else "a catalogue"
        if ident and f["kind"] in ("mover", "browndwarf"):
            who = (
                f"{ident['catalogue']}: {ident['name']}"
                + (f" ({ident['type']})" if ident.get("type") else "")
                + f", which moves like it ({ident['basis']})"
            )
        if kind == "film":  # a star or galaxy of a catalogue that happens to stand there is not the body
            jpl_said = next((i for i in idents if i["catalogue"] == "sbident" and i["state"] == "ok"), None)
            if jpl_said is not None:
                who = f"{jpl_said.get('label', 'JPL')}: {jpl_said['name']}" + (
                    f", {jpl_said['sep']:.1f}″ away" if jpl_said.get("sep") is not None else ""
                )
        base = {
            "mover": "A known object that moves like this: ",
            "browndwarf": "A known brown dwarf: ",
            "film": "A known solar-system body is predicted here: ",
            "red": "A catalogued source: ",
            "anomaly": "A catalogued source with an odd spectrum: ",
            "pair": "A catalogued variable: ",
        }.get(kind, "Known: ")
        text = base + who + "."
    else:
        none = [i.get("label", i["catalogue"]) for i in idents if i["state"] == "none"]
        if named and kind in BLEND_KINDS:
            # a catalogue does have a source at its place, only one that does not move like it: never "no catalogue names it"
            what = "a red mover" if kind == "browndwarf" else "it moves between the passes"
            return (f"{what[0].upper() + what[1:]}; {'; '.join(named[:2])} is at its place but does not move like it. "
                    "A lead to look at, not a discovery." + (f" Did not answer: {', '.join(failed)}." if failed else ""))  # fmt: skip
        text = {
            "mover": "It moves between the passes and no catalogue names it. A lead to look at, not a discovery.",
            "browndwarf": "A red mover no catalogue names: a brown dwarf candidate, not a confirmed one.",
            "film": "It moves within one pass and JPL lists no known body there. A lead, not a discovery.",
            "red": "Very red in the 4–5 µm bands and no SIMBAD source there.",
            "anomaly": "An odd spectrum and no SIMBAD source there.",
            "pair": "Two sources whose flux changes in place; no catalogue calls it a variable.",
            "peak": "A candidate of the Find Planet X search: a peak above the bar, not a detection. Its place is the orbit's place at the search's reference date.",
        }.get(kind, "No catalogue names it.")
        if none:
            text += f" Nothing within {RADIUS:.0f}″ in {', '.join(none)}."
    if failed:
        text += f" Did not answer: {', '.join(failed)}."
    return text


# ---------------------------------------------------------------- the product flow (docs: PRODUCT_SPEC §3, §5 Findings)

STAGES = ("new", "in_review", "submitted", "known", "artefact", "candidate", "confirmed")
# what the catalogues said (`status`) -> where the finding stands in the flow (`stage`) before any person has looked
STAGE_OF_STATUS = {
    "unexplained": "new",
    "unchecked": "new",
    "known": "known",
    "residual": "artefact",
    "galaxy": "artefact",
    "below": "artefact",
    "halo": "artefact",
    "chain": "artefact",  # a track through a known body (chain_of): shown, not a lead
    "blend": "artefact",  # a neighbour blending, or a survey source at its place: it did not move
    "set_aside": "artefact",  # people agreed it is an artefact (service/accounts/field_people.py)
}
VIEW_RESULTS = ("moves", "artefact", "known", "nothing", "marks")
# the promise of a finding, 0..100: what a person should look at first
# the order by kind, in bands that do not overlap (the user, 19 Nov 2026: a moving star or a brown dwarf before Planet Nine): an unexplained
# mover or brown-dwarf candidate (80 + up to 20 for its significance, so at least 90 at its 5 sigma bar) stands above every time-lapse track
# (55, at most 80 with the unseen bonus), and every track above every Planet X peak (32, at most 57); red sources and anomalies
# (0, at most 25) stay below every lead, a weak unchecked peak included. A peak stands with the tracks only
# once the link of its passes confirms it (the jump): no peak has that yet, so none does
PROMISE_BASE = {"browndwarf": 80.0, "mover": 80.0, "film": 55.0, "peak": 32.0, "pair": 25.0, "red": 0.0, "anomaly": 0.0}
PROMISE_STATUS = {"unexplained": 1.0, "unchecked": 0.8, "known": 0.25}  # anything else (a residual, a galaxy, a halo, below the bar): 0.05
PROMISE_SIGMA_MAX = 20.0
PROMISE_UNSEEN = 5.0
PROMISE_NOT_VISIBLE = 0.05  # the ring is empty in every picture that can be made (under 3 sigma): the promise is cut to a twentieth
PROMISE_LESS_CERTAIN = 15  # a time-lapse track the search itself marks "less certain" stays below every verified, visible find
PROMISING_KINDS = (
    "mover",
    "browndwarf",
    "peak",
    "film",
    "pair",
)  # the default view of the page; red sources and anomalies are behind a filter
FOUND_BY = {
    "mover": "the mover search",
    "browndwarf": "the brown dwarf search",
    "red": "the red source search",
    "anomaly": "the spectral anomaly search",
    "pair": "the changing pair search",
    "film": "the time-lapse track search",
    "peak": "the Find Planet X search",
}


def promise_of(kind: str, status: str, sigma: float | None, viewers: int) -> int:
    """promise = round(f × (base + min(20, 2σ)) + 5 if nobody has looked and f ≥ 0.8), where base is by kind in bands that do not overlap
    (mover and brown-dwarf candidate 80, time-lapse track 55, Planet X peak 32, changing pair 25, red source and spectral anomaly 0),
    σ is the find's own significance (its score, else its signal to noise), and f is by what the catalogues said (unexplained 1, not yet
    checked 0.8, known 0.25, an artefact 0.05). So an unexplained mover or brown dwarf stands above every track, and every track above
    every Planet X peak; every lead stands above every red source and anomaly."""
    f = PROMISE_STATUS.get(status, 0.05)
    s = min(PROMISE_SIGMA_MAX, 2.0 * max(0.0, float(sigma or 0.0)))
    return round(min(100.0, f * (PROMISE_BASE.get(kind, 10.0) + s) + (PROMISE_UNSEEN if viewers == 0 and f >= 0.8 else 0.0)))


class FlowDB:
    """What people did with the findings: one row per look (the product database, `SKY_PRODUCT_DB`, default `<store>/product.sqlite`;
    this table belongs to the findings). A look is recorded when a person closes the task "Check a machine finding" in the lab."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS finding_views (find_id TEXT NOT NULL, at REAL NOT NULL, result TEXT, user_id TEXT, user_name TEXT)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS finding_views_find ON finding_views(find_id)")
        self.db.commit()

    def add_view(self, find_id: str, result: str | None, user_id: str | None = None, user_name: str | None = None) -> int:
        with _READ:
            self.db.execute("INSERT INTO finding_views VALUES (?,?,?,?,?)", (find_id, time.time(), result, user_id, user_name))
            self.db.commit()
            return int(self.db.execute("SELECT COUNT(*) FROM finding_views WHERE find_id = ?", (find_id,)).fetchone()[0])

    def views(self) -> dict[str, dict[str, Any]]:
        """{find id: {viewers, results{result: n}, checked_by[names, the first first]}}."""
        out: dict[str, dict[str, Any]] = {}
        for fid, result, name in self.db.execute("SELECT find_id, result, user_name FROM finding_views ORDER BY at"):
            v = out.setdefault(fid, {"viewers": 0, "results": {}, "checked_by": []})
            v["viewers"] += 1
            if result:
                v["results"][result] = v["results"].get(result, 0) + 1
            if name and name not in v["checked_by"]:
                v["checked_by"].append(name)
        return out


def flow_fields(f: dict[str, Any], view: dict[str, Any] | None) -> dict[str, Any]:
    """The product-flow fields of a find: `stage`, `promise`, `viewers`, `credit`."""
    viewers = int((view or {}).get("viewers", 0))
    stage = STAGE_OF_STATUS.get(f["status"], "new")
    if stage == "new" and viewers > 0:
        stage = "in_review"  # a person has looked; the verdict is not given by a look alone
    return {
        "stage": stage,
        "promise": promise_of(f["kind"], f["status"], f.get("score") or f.get("snr"), viewers),
        "viewers": viewers,
        "credit": {
            "found_by": f"SPHEREx Space, {FOUND_BY.get(f['kind'], 'the search')}",
            "machine": True,
            "first_marked_by": None,
            "checked_by": list((view or {}).get("checked_by", [])),
            "looks": dict((view or {}).get("results", {})),
        },
    }


_NOTES: dict[str, str] | None = None


def machine_notes() -> dict[str, str]:
    """{find id: note}: what the review of the rendered clips saw (data/finding_notes.json). A note sets no status: it only lets the page
    show together the finds that look like an artefact, for a person to judge."""
    global _NOTES
    if _NOTES is None:
        try:
            raw = json.loads((Path(__file__).resolve().parent.parent / "data" / "finding_notes.json").read_text(encoding="utf-8"))
            _NOTES = {str(k): str(v) for k, v in (raw.get("notes") or {}).items()}
        except (OSError, ValueError):
            _NOTES = {}
    return _NOTES


def rate_unit(kind: str) -> str:
    """The unit of a find's `rate`: a time-lapse track moves within one pass (arcseconds an hour); everything else between passes
    (arcseconds a year). The number alone was read as /yr in the lab and /h in Findings for the same track."""
    return "″/h" if kind == "film" else "″/yr"


LITE_KEYS = (
    "id", "slug", "kind", "place", "fov", "ra", "dec", "rate", "rate_unit", "snr", "score", "chance", "status", "verdict", "identity", "updated",
    "stage", "promise", "viewers", "credit", "clip", "note", "visible", "visible_sn", "star_wing",
)  # fmt: skip


SLIM_KEYS = (
    "id", "kind", "place", "ra", "dec", "scans", "snr", "score", "status", "stage", "promise", "viewers", "note", "visible", "people_raised",
    "ask", "echo", "faint",
)  # fmt: skip


# the page asks for many clips and the list at once, each in its own thread: one connection is read by one thread at a time
_READ = threading.Lock()

log = logging.getLogger("sky.findings")


def find_row(db: FindingsDB, find_id: str) -> dict[str, Any] | None:
    """One find as the table keeps it (no catalogue answers): what a clip needs to know about it."""
    with _READ:
        r = db.db.execute("SELECT id, kind, place, fov, ra, dec, rate, data FROM finds WHERE id = ?", (find_id,)).fetchone()
    if r is None:
        return None
    return {**{k: r[k] for k in ("id", "kind", "place", "fov", "ra", "dec", "rate")}, "data": json.loads(r["data"] or "{}")}


def row_sig(r: Iterable[Any], idents: Iterable[Iterable[Any]]) -> bytes:
    """What a find's rows hold, in 16 bytes. Not hash(): it is the same for -1 and -2. marshal 2 writes no references, so equal rows
    give equal bytes (an equal row that did not would only be read again)."""
    return hashlib.blake2b(marshal.dumps((tuple(r), [tuple(i) for i in idents]), 2), digest_size=16).digest()


def _rows_of(db: FindingsDB, rows: Iterable[sqlite3.Row], kept: dict[str, tuple[bytes, bytes]] | None = None) -> list[Any]:
    """Each row of `finds` with its JSON read and its catalogue answers, packed on its own (marshal): [(row, idents)] packed. `kept`
    ({id: (signature, pack)}, updated here): a find whose rows hold what they held is not read again."""
    rows = list(rows)
    said = db.ident_rows_of([r["id"] for r in rows])
    out: list[Any] = []
    for r in rows:
        mine = said[r["id"]]
        sig = row_sig(r, mine)
        hit = kept.get(r["id"]) if kept is not None else None
        if hit is not None and hit[0] == sig:
            out.append(hit[1])
            continue
        got = {
            **{k: r[k] for k in r.keys() if k not in ("data", "scans", "versions")},  # noqa: SIM118 (a Row iterates its values)
            "data": json.loads(r["data"] or "{}"), "scans": json.loads(r["scans"] or "[]"), "versions": json.loads(r["versions"] or "{}"),
        }  # fmt: skip
        pack = marshal.dumps((got, [db.ident_of(i) for i in mine]))
        if kept is not None:
            kept[r["id"]] = (sig, pack)
        out.append(pack)
    return out


def read_some(db: FindingsDB, ids: list[str]) -> list[Any]:
    """read_base for a few finds (`ids=`: the lab's record of its find, a review): only their rows. Every find is judged on its own rows,
    so the finds a query returns are the same; its counts are those of these finds. One find took the whole read (6 s on prod, 20 Nov).
    Call it under _READ."""
    out: list[Any] = []
    for k in range(0, len(ids), 500):
        chunk = ids[k : k + 500]
        out += _rows_of(db, db.db.execute(f"SELECT * FROM finds WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall())
    return out


def read_base(db: FindingsDB) -> list[Any]:
    """Every find as the tables hold it (its row with the JSON read, its catalogue answers), each packed on its own: what every query
    starts from. Read once while the database is unchanged (FindingsDB.changes). One pack of the whole base (90 MB for 25,000 finds) was
    unpacked in one call that let no other thread run: the event loop stood 4–6 s on prod thirty times an hour (22 Nov 2026). The
    positions, spectra and light curves (230,000 rows) are read only for the finds a query returns. Call it under _READ."""
    sig = db.changes()
    if db._base is not None and db._base[0] == sig:
        return db._base[1]
    out: list[Any] = []
    rows = db.db.execute("SELECT * FROM finds").fetchall()
    # one catalogue's answer changes one find of 26,000: only the changed ones are read again (1.2 s to 0.4 s of JSON on the Mac). The
    # whole read was on the stacks of about two of three event loop holds on prod (23 Nov 2026); that it caused them is not shown. The
    # packs are changed in place: the caller holds _READ
    kept = db._kept_rows
    for k in range(0, len(rows), 500):
        out += _rows_of(db, rows[k : k + 500], kept)
    if len(kept) > len(rows):
        there = {r["id"] for r in rows}
        for gone in [i for i in kept if i not in there]:
            del kept[gone]
    if sig[0] >= 0 and db.changes() == sig:  # nothing was written while it was read (and the version was known)
        db._base = (sig, out)
    return out


#: the page's order of the statuses
STATUS_RANK = {
    s: i
    for i, s in enumerate(
        ("unexplained", "unchecked", "known", "residual", "galaxy", "below", "halo", "set_aside", "blend", "unverified", "chain", "weak")
    )
}


FAINT_PHOT_SN = 6.0
FAINT_SHARE = 10  # one faint peak in so many is asked about all the same, by its id: what people say of them measures the rule


def faint_peak(kind: str, data: Mapping[str, Any]) -> bool:
    """A peak of the far search on detectors 1-3 whose photometry is under 6 sigma: most of those are noise (the rule of 25 Nov 2026,
    measured for this search alone: a near peak, one of detector 4, a deep sum and a peak without the number are never faint)."""
    p = data.get("peak") or {}
    sn, far = p.get("phot_sn"), (p.get("knob") or {}).get("d") or [0.0]
    return (
        kind == "peak"
        and data.get("search") == "distant"
        and set(p.get("bydet") or {}) == {"1", "2", "3"}
        and far[0] is not None
        and far[0] >= 200.0
        and sn is not None
        and sn < FAINT_PHOT_SN
    )


def faint_sample(find_id: str) -> bool:
    return int.from_bytes(hashlib.sha1(find_id.encode()).digest()[:4], "big") % FAINT_SHARE == 0


class Snapshot:
    """Every find judged once (what the catalogues, JPL and people say of it), as the lists are made from it: `items` are the few fields
    the filters, the counts and the order read; `judge(n)` makes find n whole again."""

    def __init__(self, packed: list[Any], judge: Callable[[Any, bool], dict[str, Any]]) -> None:
        self.packed, self._judge = packed, judge
        self.items: list[dict[str, Any]] = []
        self._groups: list[tuple[dict[str, Any], int]] | None = None
        self._ordered: list[dict[str, Any]] | None = None
        self.pending = self.unavailable = self.missing_decisive = 0

    def judge(self, n: int, whole: bool = True) -> dict[str, Any]:
        return self._judge(self.packed[n], whole)

    def ordered(self) -> list[dict[str, Any]]:
        """The finds in the page's order (a status the order does not know goes last, never a KeyError)."""
        if self._ordered is None:
            self._ordered = sorted(
                self.items,
                key=lambda f: (
                    STATUS_RANK.get(f["status"], 99),
                    KINDS.index(f["kind"]) if f["kind"] in KINDS else 99,
                    -(f["score"] or f["snr"] or 0.0),
                ),
            )
        return self._ordered

    def groups(self, *, with_place: bool = False, only: frozenset[str] | None = None) -> list[tuple[dict[str, Any], int]]:
        """[(what the filters and the counts read of a find, how many finds have it)], in the order first met. Kept for the plain case."""
        if self._groups is not None and not with_place and only is None:
            return self._groups
        tally: dict[tuple[Any, ...], int] = {}
        for f in self.items:
            k = (
                f["kind"], f["status"], f["place"] if with_place else None, f["stage"], f["viewers"] == 0 and f["stage"] == "new",
                f["note"] is not None, f["visible"], only is not None and f["id"] in only, f["faint"],
            )  # fmt: skip
            tally[k] = tally.get(k, 0) + 1
        got = [
            (dict(zip(("kind", "status", "place", "stage", "waiting", "noted", "visible", "asked", "faint"), k, strict=True)), n)
            for k, n in tally.items()
        ]
        if not with_place and only is None:
            self._groups = got
        return got


def snapshot(
    db: FindingsDB,
    *,
    flow: FlowDB | None = None,
    only: frozenset[str] | None = None,
    seen: Callable[[dict[str, Any]], tuple[bool | None, float | None]] | None = None,
    aside: Mapping[str, dict[str, Any]] | None = None,
    raised: Mapping[str, str] | None = None,
    tracks: Mapping[str, dict[str, Any]] | None = None,
    names: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    real: Collection[str] = (),
) -> Snapshot:
    """The finds judged: the work every list shares, whatever its filters (see `query` for what each argument says)."""
    with _READ:
        views = flow.views() if flow is not None else {}
        packed = read_some(db, sorted(only)) if only is not None else read_base(db)
        wings = db.wings()
    order = {c: i for i, c in enumerate([*list(CATALOGUES), "sbident"])}
    hide_faint = hide_faint_peaks()

    def judge(blob: Any, whole: bool) -> dict[str, Any]:
        """One find as the page says it, from its own packed copy (a caller's change stays its own)."""
        r, ids_ = marshal.loads(blob)
        f = {
            "id": r["id"], "slug": slug_of(r["id"]), "kind": r["kind"], "place": r["place"], "fov": r["fov"], "ra": r["ra"], "dec": r["dec"], "epoch": r["epoch"],
            "rate": r["rate"], "rate_unit": rate_unit(r["kind"]), "snr": r["snr"], "score": r["score"], "chance": r["chance"],
            "data": r["data"], "scans": r["scans"], "updated": r["updated"], "versions": r["versions"],
        }  # fmt: skip
        if tracks and r["id"] in tracks:
            f = people_tracks.apply(f, tracks[r["id"]])
        have = {i["catalogue"] for i in ids_}
        for c in want_of(r["kind"], r["ra"], r["dec"], f["data"]):
            if c not in have:
                ids_.append(
                    {
                        "catalogue": c,
                        "label": CATALOGUE_LABEL[c],
                        "state": "pending",
                        "name": None,
                        "sep": None,
                        "type": None,
                        "detail": {},
                        "tried": None,
                        "attempts": 0,
                    }
                )
        ids_.sort(key=lambda i: order.get(i["catalogue"], 99))
        _motion_identity(f, ids_)
        st = status_of(f, ids_)
        said = names(f) if names is not None and f["kind"] == "peak" else None
        words: str | None = None
        if said is not None:
            f["data"].setdefault("peak", {})["known"] = said
            if said.get("state") == "ok":
                st, words = "known", said["words"][0].upper() + said["words"][1:] + "."
            elif said.get("state") == "residual":
                f["data"]["peak"]["ok"] = False  # the pages' lead rule (ok peaks) drops it
                st, words = "residual", "A residual, not a lead: " + str(said.get("reason"))
        if aside and f["id"] in aside and st in ("unexplained", "unchecked"):
            st = "set_aside"  # a catalogue's answer (known, a galaxy) stays the stronger word
            f["people_aside"] = aside[f["id"]]
        f["status"] = st
        if st == "weak" and raised and f["id"] in raised:
            st = "unexplained"  # people agree it moves: the stronger word
            f["status"] = st
        if raised and f["id"] in raised and st in ("unexplained", "unchecked"):
            f["people_raised"] = raised[f["id"]]  # moves_old: the old pictures confirm the motion; candidate: a Planet X dot
        f["idents"] = ids_
        # behind its own filter, unless a person said it is real (or people raised it); what a catalogue or JPL named is not faint
        if f["kind"] == "peak" and f["id"] in wings:
            f["star_wing"] = wings[f["id"]]  # a bright 2MASS star 25-60″ away: its glow outside the mask most likely makes the peak
        f["faint"] = (
            hide_faint
            and st in ("unexplained", "unchecked")
            and "people_raised" not in f
            and f["id"] not in real
            and (faint_peak(f["kind"], f["data"]) or "star_wing" in f)
        )
        if f["kind"] in ("red", "anomaly"):
            f["catalogue_class"] = catalogue_class(f, ids_)
            f["ask"] = ask_people(f, ids_)
        if said is not None and said.get("echo"):
            f["echo"] = True
        if whole:
            f["verdict"] = words if words is not None else verdict_of(f, ids_, st)
        f.update(flow_fields(f, views.get(f["id"])))
        f["note"] = machine_notes().get(f["id"])
        # is the object visible in its own picture (the peak inside the ring against the picture's noise)? None: not measured yet
        f["visible"], f["visible_sn"] = seen(f) if seen is not None else (None, None)
        if f["visible"] is False:
            f["promise"] = round(f["promise"] * PROMISE_NOT_VISIBLE)  # below everything the eye can see in its ring
        elif (f["data"] or {}).get("less_certain"):
            f["promise"] = min(f["promise"], PROMISE_LESS_CERTAIN)  # the search itself calls the track less certain
        return f

    # every find is judged, and only what the filters, the counts and the order read is kept of it; the finds of the page are judged again
    # whole below. Holding 25,000 whole finds (and their spectra) at once made the collector stop the other threads
    snap = Snapshot(packed, judge)
    pending = unavailable = missing_decisive = 0
    for n, blob in enumerate(packed):
        f = judge(blob, False)
        for i in f["idents"]:
            if i["state"] == "pending":
                pending += 1
        away = [i["catalogue"] for i in f["idents"] if i["state"] == "unavailable"]
        if away:
            unavailable += len(away)
            deciding = set(decisive_of(f["kind"], f["ra"], f["dec"], f["data"]))
            missing_decisive += sum(1 for c in away if c in deciding)
        one = {k: f[k] for k in SLIM_KEYS if k in f}
        one["_n"] = n
        snap.items.append(one)
    snap.pending, snap.unavailable, snap.missing_decisive = pending, unavailable, missing_decisive
    return snap


def query(
    db: FindingsDB,
    kind: str | None,
    status: str | None,
    place: str | None,
    limit: int,
    offset: int,
    *,
    flow: FlowDB | None = None,
    sort: str | None = None,
    stage: str | None = None,
    waiting: bool = False,
    kinds: tuple[str, ...] | None = None,
    clip_rank: Callable[[dict[str, Any]], int] | None = None,
    only: frozenset[str] | None = None,
    noted: bool = False,
    seen: Callable[[dict[str, Any]], tuple[bool | None, float | None]] | None = None,
    visibility: str | None = None,
    aside: Mapping[str, dict[str, Any]] | None = None,
    raised: Mapping[str, str] | None = None,
    tracks: Mapping[str, dict[str, Any]] | None = None,
    names: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    slim: bool = False,
    snap: Snapshot | None = None,
    real: Collection[str] = (),
    faint: str | None = None,
) -> dict[str, Any]:
    """The finds with what the catalogues said. `aside`: the machine finds people set aside as artefacts ({id: {look, agree, total}},
    service/accounts/field_people.set_aside): their status is `set_aside`, kept out of the leads and the default list like a halo.
    `raised`: the finds people raised up the queue ({id: why}, service/accounts/people.raised): first in the promise order.
    `tracks`: what people said about tracks (service/people_tracks.read): a corrected track is under data["people_track"]. Beside the old filters (`kind`, `status`, `place`): `kinds` (several kinds), `stage` (a
    stage of the flow), `waiting` (leads nobody has looked at yet) and `sort="promise"` (the most promising first; the default order is
    the old one). Every find also carries the flow fields (flow_fields).
    `names`: what JPL said of a Planet X peak (FpxIdentifier.verdict_on): a known body (or its echo) is `known`, a residual is `residual`,
    before every filter and count. `slim`: the finds are only what the lists of tasks need (SLIM_KEYS), never read again in full.
    `real`: the finds a person said are real (never faint). `faint`="hide": without the faint peaks (faint_peak); else they are in.
    `snap`: a snapshot of the finds already judged (the list of the page is made from one that is kept); without it one is made."""
    if snap is None:
        snap = snapshot(db, flow=flow, only=only, seen=seen, aside=aside, raised=raised, tracks=tracks, names=names, real=real)
    pending, unavailable, missing_decisive = snap.pending, snap.unavailable, snap.missing_decisive
    counts: dict[str, dict[str, int]] = {
        k: {
            "all": 0,
            "unexplained": 0,
            "unchecked": 0,
            "known": 0,
            "residual": 0,
            "galaxy": 0,
            "below": 0,
            "halo": 0,
            "set_aside": 0,
            "blend": 0,
            "unverified": 0,
            "chain": 0,
            "weak": 0,
            "leads": 0,
        }
        for k in KINDS
    }
    # what the counts read is the same for a few hundred combinations of it: they are counted once per snapshot, and a request's filters are
    # asked of a combination, not of 26,000 finds five times over
    groups = every = snap.groups(with_place=bool(place), only=only)
    if faint == "hide":
        groups = [g for g in every if not g[0]["faint"]]
    for f, n in groups:
        tally = counts.setdefault(f["kind"], dict.fromkeys(next(iter(counts.values())), 0))
        if f["status"] not in HIDDEN:  # a star's residual and a galaxy are not leads: counted apart
            tally["all"] += n
        tally[f["status"]] = tally.get(f["status"], 0) + n  # a status these lists do not know is counted, never a KeyError
        if f["status"] in ("unexplained", "unchecked"):
            tally["leads"] += n  # what is still a lead: not a known body, a galaxy, a star's residual or below the bar

    def passes(f: dict[str, Any]) -> bool:
        """Every filter of the request but the kind ones (the kind chips count under the others: counts_view)."""
        return (
            (
                (f["status"] in HIDDEN) == (status in HIDDEN) and (status not in HIDDEN or f["status"] == status)
                if status in (None, "all") or status in HIDDEN
                else f["status"] == status
            )
            and (not place or f["place"] == place)
            and (not stage or f["stage"] == stage)
            and (not waiting or f["waiting"])
            and (only is None or f["asked"])
            and (not noted or f["noted"])
            and (visibility != "seen" or f["visible"] is not False)
            and (visibility != "unseen" or f["visible"] is False)
        )

    faint_n = sum(n for f, n in every if f["faint"] and (not kind or f["kind"] == kind) and (not kinds or f["kind"] in kinds) and passes(f))
    counts_view: dict[str, int] = dict.fromkeys(KINDS, 0)
    for f, n in groups:
        if passes(f):
            counts_view[f["kind"]] = counts_view.get(f["kind"], 0) + n

    def passes_but_stage(f: dict[str, Any]) -> bool:
        """Every filter of the request but the stage ones (stage, waiting, noted): the stage chips count under the others."""
        return (
            (not kind or f["kind"] == kind)
            and (not kinds or f["kind"] in kinds)
            and (
                (f["status"] in HIDDEN) == (status in HIDDEN) and (status not in HIDDEN or f["status"] == status)
                if status in (None, "all") or status in HIDDEN
                else f["status"] == status
            )
            and (not place or f["place"] == place)
            and (only is None or f["asked"])
            and (visibility != "seen" or f["visible"] is not False)
            and (visibility != "unseen" or f["visible"] is False)
        )

    stage_view: dict[str, int] = {"waiting": 0, "noted": 0, **dict.fromkeys(STAGES, 0)}
    for f, n in groups:
        if passes_but_stage(f):
            stage_view["waiting"] += n if f["waiting"] else 0
            stage_view["noted"] += n if f["noted"] else 0
            stage_view[f["stage"]] = stage_view.get(f["stage"], 0) + n
    sel = [
        f
        for f in snap.ordered()  # the page's order, sorted once per snapshot: forty lists sorted 14,000 finds forty times
        if (not kind or f["kind"] == kind)
        and (
            True  # "any": every find, the hidden ones too (the question series' control objects; not a status of the page)
            if status == "any"
            else (f["status"] in HIDDEN) == (status in HIDDEN) and (status not in HIDDEN or f["status"] == status)
            if status in (None, "all") or status in HIDDEN
            else f["status"] == status
        )
        and (not place or f["place"] == place)
        and (not kinds or f["kind"] in kinds)
        and (not stage or f["stage"] == stage)
        and (not waiting or (f["viewers"] == 0 and f["stage"] == "new"))
        and (only is None or f["id"] in only)
        and (not noted or f["note"] is not None)
        and (faint != "hide" or not f["faint"])
    ]

    def vis_of(f: dict[str, Any]) -> str:
        return "visible" if f["visible"] else "not_visible" if f["visible"] is False else "not_measured"

    by_kind: dict[str, dict[str, int]] = {}
    for f, n in groups:
        if f["status"] not in HIDDEN:
            t = by_kind.setdefault(f["kind"], {"visible": 0, "not_visible": 0, "not_measured": 0})
            t[vis_of(f)] += n
    in_view = {"visible": 0, "not_visible": 0, "not_measured": 0}
    for f in sel:
        in_view[vis_of(f)] += 1
    # "seen" is every find not known to be invisible: visible in its picture, or its picture not measured yet (a fresh store renders
    # its clips in the background for hours, and the page must not be empty meanwhile); "unseen" is only the measured not visible
    if visibility == "seen":
        sel = [f for f in sel if f["visible"] is not False]
    elif visibility == "unseen":
        sel = [f for f in sel if f["visible"] is False]
    if sort == "promise":
        # a find that can have no clip (a track whose visits are not kept, a place no field covers) goes below every find that can;
        # within equal promise the finds whose clip is ready come first: the first screen shows pictures, not empty cards
        cr: dict[str, int] = {}
        if clip_rank is not None:
            for f in sel:
                if "_clip" not in f:
                    f["_clip"] = clip_rank(snap.judge(f["_n"], False))  # kept with the snapshot
                cr[f["id"]] = f["_clip"]
        # ... and a find that carries a machine note (it looks like an artefact) goes below every clean find
        # ... and a find people raised (they agree it is worth the slow checks) goes before every other
        sel.sort(
            key=lambda f: (
                "people_raised" not in f,
                cr.get(f["id"], 0) == 2,
                f["note"] is not None,
                -f["promise"],
                cr.get(f["id"], 0),
                -(f["score"] or f["snr"] or 0.0),
                f["id"],
            )
        )
    shown = [(f, n) for f, n in groups if f["status"] not in HIDDEN]
    stages = {s: sum(n for f, n in groups if f["stage"] == s) for s in STAGES}
    page = sel[offset : offset + limit]
    if slim:
        finds = [dict(f) for f in page]  # a caller's change stays its own: the snapshot and its groups are shared
    else:
        finds = [snap.judge(f["_n"]) for f in page]
        with _READ:
            rel = db.related([f["id"] for f in finds])
        for f in finds:
            f.update(rel[f["id"]])
    return {
        "count": len(sel),
        "faint": faint_n,  # the faint peaks under the request's other filters, shown or hidden
        "total": sum(n for _, n in shown),
        "counts": counts,
        # per kind, the finds that pass this request's every filter but the kind ones (status, stage, waiting, visibility, …): the
        # numbers of the kind chips under the filters in use
        "counts_view": counts_view,
        # the stage chips' numbers (waiting for a look, noted, each stage) under the request's every filter but the stage ones
        "stage_view": stage_view,
        "labels": KIND_LABEL,
        "stages": stages,
        "waiting": sum(n for f, n in shown if f["waiting"]),
        "waiting_promising": sum(n for f, n in shown if f["waiting"] and f["kind"] in PROMISING_KINDS),
        "promising": sum(n for f, n in shown if f["kind"] in PROMISING_KINDS),
        "promising_kinds": list(PROMISING_KINDS),
        "visibility": {"by_kind": by_kind, "in_view": in_view},
        "noted": sum(n for f, n in shown if f["noted"]),
        "identification": {
            "pending": pending,
            "unavailable": unavailable,
            "unavailable_decisive": missing_decisive,
            "unavailable_extras": unavailable - missing_decisive,
            "catalogues": [CATALOGUE_LABEL[c] for c in CATALOGUES] + [CATALOGUE_LABEL["sbident"]],
        },
        "finds": finds,
    }
