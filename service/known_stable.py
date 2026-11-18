"""A field's known objects as one stable set, for the lab's rings (`GET /api/known/field`).

The lab used to ring a catalogue object only when its own peak finder found a source at its place in the pass on show: noise moves
peaks from pass to pass, so rings blinked ("known 32/34" alternating) and stars fainter than the drawn catalogue had no ring at all.
Here the matching is done once per field, against what the field really shows:

    measured   the sources found in at least half of the field's passes (and at least STABLE_MIN_PASSES), at their mean place
               (engine.movers.find_sources on each pass's picture; a worker process)
    catalogue  service.known_field at the field's epoch (the middle pass): the drawn objects (Gaia to G 19, SIMBAD) and the deeper
               match-only sources (Gaia to G 21, CatWISE2020, AllWISE, 2MASS)
    seen       a catalogue source with a measured source within OBJ_MATCH_ARCSEC (a deep one within DEEP_MATCH_ARCSEC)
    depth      per magnitude band, the faintest magnitude the field shows: the DEPTH_PERCENTILE of the seen sources' magnitudes
    unmatched  measured sources of S/N >= UNMATCHED_SNR with no catalogue source near and not in a bright star's glow

JPL bodies are not in the set: they move between passes (the lab keeps drawing them per pass from `GET /api/known`). The measured set
is kept per field (it only changes when the field is made again); the catalogue part is asked like `GET /api/known` (cached per frame;
a catalogue still being asked is `pending`, and `complete` is false until every one answered).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from engine.grid import Grid
from engine.movers import find_sources
from service.products.sxp import decode
from service.storage import atomic_write_bytes

VERSION = "s1"  # of the kept measured set: a new detection rule is a new version
STABLE_MIN_PASSES = 2
OBJ_MATCH_ARCSEC = 7.0  # the lab's MATCH_ARCSEC: a SPHEREx pixel is 6.2″
DEEP_MATCH_ARCSEC = 3.0  # the lab's DEEP_ARCSEC: the deep catalogues' places are good to an arcsecond
CLUSTER_ARCSEC = 3.0  # one source seen in several passes: its places agree within this
UNMATCHED_SNR = 8.0  # the lab's NOMATCH_SIGMA
DEPTH_PERCENTILE = 90.0
DEPTH_MIN_SEEN = 5  # fewer seen sources of a band: no depth said for it


def measured_sources(raws: list[bytes], grid: dict[str, Any]) -> list[dict[str, Any]]:
    """The sources found in at least half of the passes (and at least STABLE_MIN_PASSES): [{ra, dec, snr, passes, saturated}]
    (module level: it runs in a worker process)."""
    g = Grid(float(grid["ra0"]), float(grid["dec0"]), float(grid["step_arcsec"]), int(grid["n"]))
    rows: list[tuple[float, float, int, float, bool]] = []
    for k, raw in enumerate(raws):
        _, arr = decode(raw)
        white = arr["white"].astype(np.float32)
        sat = arr["sat"].astype(np.float32) / 255.0
        rows.extend((s.x, s.y, k, s.snr, s.saturated) for s in find_sources(white, sat, g.step_arcsec))
    n_passes = len(raws)
    need = max(STABLE_MIN_PASSES, math.ceil(n_passes / 2))
    if not rows or n_passes < need:
        return []
    xy = np.array([(r[0], r[1]) for r in rows])
    parent = list(range(len(rows)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in cKDTree(xy).query_pairs(max(1.0, CLUSTER_ARCSEC / g.step_arcsec)):
        parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(rows)):
        groups.setdefault(root(i), []).append(i)
    keep: list[tuple[float, float, float, int, bool]] = []
    for members in groups.values():
        passes = {rows[i][2] for i in members}
        if len(passes) >= need:
            keep.append(
                (
                    float(np.mean([rows[i][0] for i in members])),
                    float(np.mean([rows[i][1] for i in members])),
                    float(np.median([rows[i][3] for i in members])),
                    len(passes),
                    any(rows[i][4] for i in members),
                )
            )
    if not keep:
        return []
    ra, dec = g.grid_to_sky([k[0] for k in keep], [k[1] for k in keep])
    return [
        {"ra": round(float(a) % 360.0, 6), "dec": round(float(d), 6), "snr": round(k[2], 1), "passes": k[3], "saturated": k[4]}
        for k, a, d in zip(keep, ra, dec, strict=True)
    ]


def kept_path(store_dir: Path, field_key: str) -> Path:
    return Path(store_dir) / "known_stable" / f"{field_key}-{VERSION}.json"


def read_kept(store_dir: Path, field_key: str) -> list[dict[str, Any]] | None:
    try:
        got: list[dict[str, Any]] = json.loads(kept_path(store_dir, field_key).read_text())
        return got
    except (OSError, ValueError):
        return None


def keep(store_dir: Path, field_key: str, measured: list[dict[str, Any]]) -> None:
    p = kept_path(store_dir, field_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(p, json.dumps(measured, separators=(",", ":")).encode())


def _xyz(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    r, d = np.radians(ra), np.radians(dec)
    return np.column_stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])


def _chord(arcsec: float) -> float:
    return 2.0 * math.sin(math.radians(arcsec / 3600.0) / 2.0)


def stable_set(measured: list[dict[str, Any]], known: dict[str, Any], epoch: float) -> dict[str, Any]:
    """The catalogue sources at the field's epoch, each seen or not, the depth per band, and the measured sources no catalogue has.
    `known`: the answer of service.known_field (objects and match, at the one epoch `epoch`)."""

    def at(o: dict[str, Any]) -> tuple[float, float] | None:
        for p in o.get("positions") or []:
            if abs(float(p.get("epoch", epoch)) - epoch) < 1e-6 or len(o["positions"]) == 1:
                return float(p["ra"]), float(p["dec"])
        return None

    cat: list[tuple[dict[str, Any], float, float, bool]] = []  # (object, ra, dec, deep)
    for o in known.get("objects") or []:
        if o.get("source") == "jpl":
            continue  # it moves between passes: drawn per pass by the lab
        p = at(o)
        if p is not None:
            cat.append((o, p[0], p[1], False))
    for m in known.get("match") or []:
        p = at(m)
        if p is not None:
            cat.append((m, p[0], p[1], True))
    tree = cKDTree(_xyz(np.array([m["ra"] for m in measured]), np.array([m["dec"] for m in measured]))) if measured else None
    sources: list[dict[str, Any]] = []
    for o, ra, dec, deep in cat:
        hit_snr: float | None = None
        sep: float | None = None
        if tree is not None:
            d, i = tree.query(
                _xyz(np.array([ra]), np.array([dec]))[0], distance_upper_bound=_chord(DEEP_MATCH_ARCSEC if deep else OBJ_MATCH_ARCSEC)
            )
            if math.isfinite(d):
                hit_snr = float(measured[int(i)]["snr"])
                sep = round(math.degrees(2.0 * math.asin(min(1.0, d / 2.0))) * 3600.0, 1)
        band = o.get("mag_band") or ("W1" if deep else None)
        src = {
            "id": o["id"],
            "source": "deep" if deep else o.get("source"),
            "ra": round(ra, 6),
            "dec": round(dec, 6),
            "mag": o.get("mag"),
            "mag_band": band,
            "seen": hit_snr is not None,
            **({"snr": hit_snr, "sep_arcsec": sep} if hit_snr is not None else {}),
            **({"catalogues": o.get("catalogues")} if deep else {}),
            **({"name": o.get("name"), "label": True} if o.get("label") else {}),
        }
        sources.append(src)
    depth: dict[str, float] = {}
    for band in {s["mag_band"] for s in sources if s["mag_band"]}:
        mags = [float(s["mag"]) for s in sources if s["seen"] and s["mag_band"] == band and s["mag"] is not None]
        if len(mags) >= DEPTH_MIN_SEEN:
            depth[band] = round(float(np.percentile(mags, DEPTH_PERCENTILE)), 2)
    # unseen objects are kept only when they are as bright as the field shows (they should be there: blended, or in a glow)
    out = [
        s
        for s in sources
        if s["seen"] or s.get("label") or (s["source"] != "deep" and s["mag"] is not None and s["mag"] <= depth.get(s["mag_band"], -99.0))
    ]
    # the measured sources no catalogue source is near, and not in a bright star's glow (its halo, spikes and ghosts)
    unmatched: list[dict[str, Any]] = []
    if measured and cat:
        ctree = cKDTree(_xyz(np.array([c[1] for c in cat]), np.array([c[2] for c in cat])))
        bright = [
            (c[1], c[2], 120.0 if c[0]["mag"] <= 6 else 75.0 if c[0]["mag"] <= 9 else 30.0)
            for c in cat
            if not c[3] and c[0].get("mag") is not None and c[0]["mag"] <= 10
        ]
        for m in measured:
            if m["snr"] < UNMATCHED_SNR:
                continue
            if ctree.query_ball_point(_xyz(np.array([m["ra"]]), np.array([m["dec"]]))[0], _chord(OBJ_MATCH_ARCSEC)):
                continue
            if any(math.hypot((m["ra"] - b[0]) * math.cos(math.radians(m["dec"])), m["dec"] - b[1]) * 3600.0 < b[2] for b in bright):
                continue
            unmatched.append(m)
    elif measured:
        unmatched = [m for m in measured if m["snr"] >= UNMATCHED_SNR]
    return {"sources": out, "depth": depth, "unmatched": unmatched, "measured": len(measured)}
