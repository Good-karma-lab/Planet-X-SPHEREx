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
import math
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from service import people_tracks
from service.bodies import SB_IDENT, _iso, _sexa_dec, _sexa_ra
from service.products.store import Store
from service.products.versions import VERSIONS
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
HIDDEN = ("residual", "galaxy", "below", "halo", "set_aside")  # statuses kept out of the leads and the default list


def is_galaxy(name: str | None, typ: str | None) -> bool:
    return (typ or "") in GALAXY_TYPES or (name or "").lstrip("[]* ").startswith(GALAXY_NAMES)


RADIUS = 6.0  # arcsec: how far from a find a catalogue entry may stand and still be named
SWEEP_S = 120.0  # a sweep reads every product file (each parse holds the GIL): new finds also kick it at once
RETRY_S = 90.0
MAX_ATTEMPTS = 8
RESULT_DIRS = ("movers", "pairs", "browndwarfs", "red", "anomalies")


def _cell(ra: float, dec: float) -> str:
    c = math.cos(math.radians(dec))
    return f"{round(ra * c * 3600 / 6)}:{round(dec * 3600 / 6)}"


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
        if row["kind"] == "field" and row["complete"]:
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
            for c in (q for q in meta.get("candidates", []) if q.get("ok")):
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
    for f in finds:
        if any(h["id"] == f["id"] for h in have):
            taken.add(f["id"])
    for f in finds:
        if f["id"] in taken:
            continue
        for h in have:
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
        self._migrate()

    def _migrate(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
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
        """Finds with a catalogue not yet answered (or that did not answer and may be asked again). The few that matter most are asked first
        (Find Planet X peaks, movers, brown dwarfs, film tracks, then pairs; the hundreds of anomalies and red sources last), the test targets'
        before the rest within each rank, and within that the longest waiting."""
        now = time.time()
        rows = self.db.execute("SELECT id, kind, place, ra, dec, data FROM finds").fetchall()
        out: list[tuple[tuple[int, int, float], sqlite3.Row]] = []
        done = {(r["find_id"], r["catalogue"]): r for r in self.db.execute("SELECT * FROM idents")}
        for r in rows:
            want = want_of(r["kind"], r["ra"], r["dec"], json.loads(r["data"] or "{}"))
            worst = None
            for c in want:
                d = done.get((r["id"], c))
                if d is None:
                    worst = 0.0 if worst is None else min(worst, 0.0)
                elif (
                    d["state"] == "unavailable"
                    and d["attempts"] < MAX_ATTEMPTS
                    and now - d["tried"] >= RETRY_S * (2 ** min(d["attempts"], 5))
                ):
                    worst = d["tried"] if worst is None else min(worst, d["tried"])
            if worst is not None:
                out.append(((IDENT_RANK.get(r["kind"], 2), 0 if r["place"] in targets else 1, worst), r))
        out.sort(key=lambda x: x[0])
        return [r for _, r in out[:limit]]

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

    def idents_of(self, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
        for k in range(0, len(ids), 500):
            chunk = ids[k : k + 500]
            marks = ",".join("?" for _ in chunk)
            for r in self.db.execute(f"SELECT * FROM idents WHERE find_id IN ({marks})", chunk):
                out[r["find_id"]].append(
                    {
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
                )
        return out


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
        try:
            self.targets = [
                {"n": o["n"], "ra": o["ra"], "dec": o["dec"]}
                for o in json.loads((Path(__file__).resolve().parent.parent / "data" / "catalog.json").read_text())["catalog"]
                if o.get("b")
            ]
        except (OSError, ValueError, KeyError):
            self.targets = []

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
            self.tasks = [asyncio.create_task(self.sweep_loop()), asyncio.create_task(self.identify_loop())]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()

    def notify(self) -> None:
        """A scan finished (hook for the scan workers): sweep now instead of at the next tick."""
        self.kick.set()

    async def sweep(self) -> dict[str, int]:
        rows = self.store.listing(("field", "filmsearch", "distant", "deep"))
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
        finds, sig = await asyncio.to_thread(collect, self.store_dir, rows, fkeys, self.targets)
        if sig == self.db.get_meta("sig"):
            return {"added": 0, "removed": 0, "finds": len(finds)}
        async with self.lock:
            have = await asyncio.to_thread(lambda: [dict(r) for r in self.db.db.execute("SELECT id, kind, ra, dec, rate FROM finds")])
            reuse_ids(finds, have)
            added, removed = await asyncio.to_thread(self.db.write_finds, finds)
            self.db.set_meta("sig", sig)
        return {"added": added, "removed": removed, "finds": len(finds)}

    async def sweep_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.sweep()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.kick.wait(), SWEEP_S)
            self.kick.clear()

    async def sbident(
        self, ra: float, dec: float, mjd: float, rate: float | None, pa: float | None, check_rate: bool = True
    ) -> dict[str, Any]:
        """JPL's Small-Body Identification at the place and time of a moving find (the position at that very time). A body counts only if it
        stands within 15″, is brighter than V 21, and moves at the find's rate (25 %) and direction (30°); the nearest such body is named with
        both rates. When bodies stand near but none moves like the find, the answer is `none` and says what the nearest was."""
        half = 60.0 / 3600.0
        if getattr(self, "sb_gate", None) is None:
            self.sb_gate = asyncio.Semaphore(1)
        async with self.sb_gate:
            return await self._sbident_ask(ra, dec, mjd, rate, pa, check_rate, half)

    async def _sbident_ask(
        self, ra: float, dec: float, mjd: float, rate: float | None, pa: float | None, check_rate: bool, half: float
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
        fields = body.get("fields_first") or body.get("fields") or []
        data = body.get("data_first_pass") or body.get("data") or []
        idx = {n: i for i, n in enumerate(fields)}
        c_ra, c_dec = 'Dist. from center RA (")', 'Dist. from center Dec (")'
        r_ra, r_dec, c_v = 'RA rate ("/h)', 'Dec rate ("/h)', "Visual magnitude (V)"
        near: list[tuple[float, list[Any]]] = []
        for row in data:
            try:
                near.append((math.hypot(float(row[idx[c_ra]]), float(row[idx[c_dec]])), row))
            except (KeyError, ValueError, IndexError):
                continue
        near.sort(key=lambda x: x[0])
        out: dict[str, Any] = {"n_bodies": len(data), "vmag_limit": SB_VMAG}
        for sep, row in near:
            if sep > SB_MATCH_ARCSEC:
                break
            try:
                vr, vd = float(row[idx[r_ra]]), float(row[idx[r_dec]])
            except (KeyError, ValueError, IndexError):
                out.update(state="none", note=f"{row[0]} stands {sep:.1f}″ away but JPL gave no rate to compare")
                continue
            speed, ang = math.hypot(vr, vd), math.degrees(math.atan2(vr, vd)) % 360.0
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
        """JPL at the dates of the find's visits. A film track: three of its visits (first, middle, last), the same body at two of three at the
        track's rate; a brown dwarf candidate near the ecliptic: the same body within 15″ at every pass date."""
        d = json.loads(row["data"])
        trk = d.get("track") or {}
        if row["kind"] == "film":
            vis = d.get("visits") or []
            sel = [vis[i] for i in sorted({0, len(vis) // 2, len(vis) - 1})] if vis else []
            rate, pa, check, need = _fnum(trk.get("rate")), _fnum(trk.get("pa")), True, max(1, math.ceil(len(sel) * 2 / 3))
        elif row["kind"] == "peak":
            ref = _ref_mjd("peak", d)
            sel = [{"ra": row["ra"], "dec": row["dec"], "mjd": ref}] if ref is not None else []
            rate, pa, check, need = None, None, False, 1
        else:
            sel = [{"ra": row["ra"], "dec": row["dec"], "mjd": m} for m in (d.get("mjd") or []) if m is not None]
            rate, pa, check, need = None, None, False, len(sel)
        if not sel:
            return {"state": "unavailable", "error": "no visit dates"}
        got = [await self.sbident(v["ra"], v["dec"], v["mjd"], rate, pa, check) for v in sel]
        names = [g["name"] for g in got if g.get("state") == "ok"]
        best = max(set(names), key=names.count) if names else None
        per = [
            {"mjd": v["mjd"], "state": g["state"], "name": g.get("name"), "sep": g.get("sep"), "note": g.get("note")}
            for v, g in zip(sel, got, strict=True)
        ]
        if best is not None and names.count(best) >= need:
            hit = next(g for g in got if g.get("name") == best)
            return {**hit, "visits_checked": len(sel), "visits_matched": names.count(best), "per_visit": per}
        return {
            "state": "none",
            "visits_checked": len(sel),
            "visits_matched": names.count(best) if best else 0,
            "per_visit": per,
            "n_bodies": max(g.get("n_bodies", 0) for g in got),
        }

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

    async def identify_one(self, row: sqlite3.Row) -> None:
        want = want_of(row["kind"], row["ra"], row["dec"], json.loads(row["data"] or "{}"))
        if "sbident" in want:
            try:
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
                    "visits_checked",
                    "visits_matched",
                    "per_visit",
                )
                self.db.put_ident(row["id"], "sbident", a["state"], a.get("name"), a.get("sep"), None, {k: a[k] for k in keep if k in a}, a)
            except BreakerOpenError:
                pass  # JPL is resting after failures: the find stays pending and is asked later, no attempt spent
            except (httpx.HTTPError, ValueError) as e:
                self.db.put_ident(row["id"], "sbident", "unavailable", None, None, None, {"error": str(e)[:120]})
        if "halo" in want:
            try:
                h = await self.bright_neighbour(row["ra"], row["dec"])
                self.db.put_ident(row["id"], "halo", h["state"], h.get("name"), h.get("sep"), None, {k: h[k] for k in ("g",) if k in h}, h)
            except (httpx.HTTPError, BreakerOpenError, ValueError, RuntimeError) as e:
                self.db.put_ident(row["id"], "halo", "unavailable", None, None, None, {"error": str(e)[:120]})
        if "simbad" in want:
            ctx = await self.catalogue.context(row["ra"], row["dec"], EPOCH, RADIUS)
            for cat in CATALOGUES:
                a = ctx.get(cat) or {"state": "unavailable", "error": "no answer"}
                state, name, sep, typ, detail = _ident_from(cat, a)
                self.db.put_ident(row["id"], cat, state, name, sep, typ, detail, a)
        self.db.db.commit()

    async def identify_loop(self) -> None:
        while True:
            try:
                batch = await asyncio.to_thread(self.db.pending, 8, frozenset(t["n"] for t in self.targets))
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
        known = d.get("group") == "known" or _pm_agrees(f, idents) or (ok("simbad") and "BD" in str(by["simbad"].get("type") or ""))
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
        # a catalogue that did not answer after every retry counts as asked (the verdict says it did not answer); one still being retried does not
        return st == "pending" or (st == "unavailable" and int(i.get("attempts") or 0) < MAX_ATTEMPTS)

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
    "set_aside": "artefact",  # people agreed it is an artefact (service/accounts/field_people.py)
}
VIEW_RESULTS = ("moves", "artefact", "known", "nothing", "marks")
# the promise of a finding, 0..100: what a person should look at first
PROMISE_BASE = {"browndwarf": 70.0, "peak": 65.0, "mover": 60.0, "film": 50.0, "pair": 30.0, "red": 10.0, "anomaly": 10.0}
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
    """promise = round(f × (base + min(20, 2σ)) + 5 if nobody has looked and f ≥ 0.8), where base is by kind (brown dwarf candidate 70,
    Planet X peak 65, mover 60, time-lapse track 50, changing pair 30, red source 10, spectral anomaly 10), σ is the find's own
    significance (its score, else its signal to noise), and f is by what the catalogues said (unexplained 1, not yet checked 0.8,
    known 0.25, an artefact 0.05). So an unexplained mover the catalogues do not name stands above every known one, and every mover,
    dwarf, peak and track that is still a lead stands above every red source and anomaly."""
    f = PROMISE_STATUS.get(status, 0.05)
    s = min(PROMISE_SIGMA_MAX, 2.0 * max(0.0, float(sigma or 0.0)))
    return round(f * (PROMISE_BASE.get(kind, 10.0) + s) + (PROMISE_UNSEEN if viewers == 0 and f >= 0.8 else 0.0))


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
    "id", "kind", "place", "fov", "ra", "dec", "rate", "rate_unit", "snr", "score", "chance", "status", "verdict", "identity", "updated",
    "stage", "promise", "viewers", "credit", "clip", "note", "visible", "visible_sn",
)  # fmt: skip


# the page asks for many clips and the list at once, each in its own thread: one connection is read by one thread at a time
_READ = threading.Lock()


def find_row(db: FindingsDB, find_id: str) -> dict[str, Any] | None:
    """One find as the table keeps it (no catalogue answers): what a clip needs to know about it."""
    with _READ:
        r = db.db.execute("SELECT id, kind, place, fov, ra, dec, rate, data FROM finds WHERE id = ?", (find_id,)).fetchone()
    if r is None:
        return None
    return {**{k: r[k] for k in ("id", "kind", "place", "fov", "ra", "dec", "rate")}, "data": json.loads(r["data"] or "{}")}


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
) -> dict[str, Any]:
    """The finds with what the catalogues said. `aside`: the machine finds people set aside as artefacts ({id: {look, agree, total}},
    service/accounts/field_people.set_aside): their status is `set_aside`, kept out of the leads and the default list like a halo.
    `raised`: the finds people raised up the queue ({id: why}, service/accounts/people.raised): first in the promise order.
    `tracks`: what people said about tracks (service/people_tracks.read): a corrected track is under data["people_track"]. Beside the old filters (`kind`, `status`, `place`): `kinds` (several kinds), `stage` (a
    stage of the flow), `waiting` (leads nobody has looked at yet) and `sort="promise"` (the most promising first; the default order is
    the old one). Every find also carries the flow fields (flow_fields)."""
    with _READ:
        views = flow.views() if flow is not None else {}
        rows = db.db.execute("SELECT * FROM finds").fetchall()
        ids = [r["id"] for r in rows]
        idents = db.idents_of(ids)
        rel = db.related(ids)
    items: list[dict[str, Any]] = []
    for r in rows:
        f = {
            "id": r["id"], "kind": r["kind"], "place": r["place"], "fov": r["fov"], "ra": r["ra"], "dec": r["dec"], "epoch": r["epoch"],
            "rate": r["rate"], "rate_unit": rate_unit(r["kind"]), "snr": r["snr"], "score": r["score"], "chance": r["chance"],
            "data": json.loads(r["data"] or "{}"), "scans": json.loads(r["scans"] or "[]"), "updated": r["updated"],
            "versions": json.loads(r["versions"] or "{}"), **rel[r["id"]],
        }  # fmt: skip
        if tracks and r["id"] in tracks:
            f = people_tracks.apply(f, tracks[r["id"]])
        ids_ = idents[r["id"]]
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
        order = {c: i for i, c in enumerate([*list(CATALOGUES), "sbident"])}
        ids_.sort(key=lambda i: order.get(i["catalogue"], 99))
        _motion_identity(f, ids_)
        st = status_of(f, ids_)
        if aside and f["id"] in aside and st in ("unexplained", "unchecked"):
            st = "set_aside"  # a catalogue's answer (known, a galaxy) stays the stronger word
            f["people_aside"] = aside[f["id"]]
        f["status"] = st
        if raised and f["id"] in raised and st in ("unexplained", "unchecked"):
            f["people_raised"] = raised[f["id"]]  # moves_old: the old pictures confirm the motion; candidate: a Planet X dot
        f["idents"] = ids_
        f["verdict"] = verdict_of(f, ids_, st)
        f.update(flow_fields(f, views.get(f["id"])))
        f["note"] = machine_notes().get(f["id"])
        # is the object visible in its own picture (the peak inside the ring against the picture's noise)? None: not measured yet
        f["visible"], f["visible_sn"] = seen(f) if seen is not None else (None, None)
        if f["visible"] is False:
            f["promise"] = round(f["promise"] * PROMISE_NOT_VISIBLE)  # below everything the eye can see in its ring
        elif (f["data"] or {}).get("less_certain"):
            f["promise"] = min(f["promise"], PROMISE_LESS_CERTAIN)  # the search itself calls the track less certain
        items.append(f)
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
            "leads": 0,
        }
        for k in KINDS
    }
    for f in items:
        tally = counts.setdefault(
            f["kind"],
            {
                "all": 0,
                "unexplained": 0,
                "unchecked": 0,
                "known": 0,
                "residual": 0,
                "galaxy": 0,
                "below": 0,
                "halo": 0,
                "set_aside": 0,
                "leads": 0,
            },
        )
        if f["status"] not in HIDDEN:  # a star's residual and a galaxy are not leads: counted apart
            tally["all"] += 1
        tally[f["status"]] += 1
        if f["status"] in ("unexplained", "unchecked"):
            tally["leads"] += 1  # what is still a lead: not a known body, a galaxy, a star's residual or below the bar
    sel = [
        f
        for f in items
        if (not kind or f["kind"] == kind)
        and (
            (f["status"] in HIDDEN) == (status in HIDDEN) and (status not in HIDDEN or f["status"] == status)
            if status in (None, "all") or status in HIDDEN
            else f["status"] == status
        )
        and (not place or f["place"] == place)
        and (not kinds or f["kind"] in kinds)
        and (not stage or f["stage"] == stage)
        and (not waiting or (f["viewers"] == 0 and f["stage"] == "new"))
        and (only is None or f["id"] in only)
        and (not noted or f["note"] is not None)
    ]

    def vis_of(f: dict[str, Any]) -> str:
        return "visible" if f["visible"] else "not_visible" if f["visible"] is False else "not_measured"

    by_kind: dict[str, dict[str, int]] = {}
    for f in items:
        if f["status"] not in HIDDEN:
            t = by_kind.setdefault(f["kind"], {"visible": 0, "not_visible": 0, "not_measured": 0})
            t[vis_of(f)] += 1
    in_view = {"visible": 0, "not_visible": 0, "not_measured": 0}
    for f in sel:
        in_view[vis_of(f)] += 1
    # "seen" is every find not known to be invisible: visible in its picture, or its picture not measured yet (a fresh store renders
    # its clips in the background for hours, and the page must not be empty meanwhile); "unseen" is only the measured not visible
    if visibility == "seen":
        sel = [f for f in sel if f["visible"] is not False]
    elif visibility == "unseen":
        sel = [f for f in sel if f["visible"] is False]
    rank = {"unexplained": 0, "unchecked": 1, "known": 2, "residual": 3, "galaxy": 4, "below": 5, "halo": 6, "set_aside": 7}
    sel.sort(key=lambda f: (rank[f["status"]], KINDS.index(f["kind"]) if f["kind"] in KINDS else 99, -(f["score"] or f["snr"] or 0.0)))
    if sort == "promise":
        # a find that can have no clip (a track whose visits are not kept, a place no field covers) goes below every find that can;
        # within equal promise the finds whose clip is ready come first: the first screen shows pictures, not empty cards
        cr = {f["id"]: clip_rank(f) for f in sel} if clip_rank is not None else {}
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
    shown = [f for f in items if f["status"] not in HIDDEN]
    stages = {s: sum(1 for f in items if f["stage"] == s) for s in STAGES}
    pending = sum(1 for f in items for i in f["idents"] if i["state"] == "pending")
    unavailable = sum(1 for f in items for i in f["idents"] if i["state"] == "unavailable")
    deciding = {f["id"]: set(decisive_of(f["kind"], f["ra"], f["dec"], f["data"])) for f in items}
    missing_decisive = sum(1 for f in items for i in f["idents"] if i["state"] == "unavailable" and i["catalogue"] in deciding[f["id"]])
    return {
        "count": len(sel),
        "total": sum(1 for f in items if f["status"] not in HIDDEN),
        "counts": counts,
        "labels": KIND_LABEL,
        "stages": stages,
        "waiting": sum(1 for f in shown if f["viewers"] == 0 and f["stage"] == "new"),
        "waiting_promising": sum(1 for f in shown if f["viewers"] == 0 and f["stage"] == "new" and f["kind"] in PROMISING_KINDS),
        "promising": sum(1 for f in shown if f["kind"] in PROMISING_KINDS),
        "promising_kinds": list(PROMISING_KINDS),
        "visibility": {"by_kind": by_kind, "in_view": in_view},
        "noted": sum(1 for f in shown if f["note"] is not None),
        "identification": {
            "pending": pending,
            "unavailable": unavailable,
            "unavailable_decisive": missing_decisive,
            "unavailable_extras": unavailable - missing_decisive,
            "catalogues": [CATALOGUE_LABEL[c] for c in CATALOGUES] + [CATALOGUE_LABEL["sbident"]],
        },
        "finds": sel[offset : offset + limit],
    }
