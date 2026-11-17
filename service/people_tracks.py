"""Tracks as people corrected them (spec §5g; service/accounts/people.py): a track find's visits are re-linked with what people agreed.

  missing     the visits people agreed the dot is missing from (people.missing_visits): the track is fitted again without them;
  recentre    taps on the object's centre in a visit (people.taps(..., "recentre")): a visit two or more people tapped takes the
              median of their taps as its place;
  find_again  taps on the lead in another pass (people.taps(..., "find_again")): where two or more people tapped within
              AGREE_ARCSEC of each other, that place joins the fit, if the track still fits as well (rms at most RMS_GROWTH times).

The machine's own track stays in the find's data; what people changed is under data["people_track"] with the refitted rate, the
position angle, the rms and the visits used. A find left with fewer than two visits is said so ("too few visits left").
A tap is matched to a visit by its date (within MATCH_DAYS).
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median
from typing import Any

MATCH_DAYS = 0.02  # a tap belongs to the visit this close in time (half an hour)
AGREE_ARCSEC = 10.0  # find_again taps of different people this close are the same place
RMS_GROWTH = 2.0  # a found-again place joins only when the refitted rms stays within this factor (and RMS_FLOOR)
RMS_FLOOR = 2.0  # arcsec


def read(st: Any) -> dict[str, dict[str, Any]]:
    """Everything people said about tracks, in bulk: {finding_id: {missing, recentre, find_again}} (a database read: in a thread)."""
    from service import accounts
    from service.accounts import people

    db = accounts.get(st).db
    people.get(st, db)  # the tables exist
    c = db._conn()
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"missing": set(), "recentre": [], "find_again": []})
    for fid, v in people._action(st, "relink").items():
        out[fid]["missing"] = {int(i) for i in v.get("missing", [])}
    for r in c.execute(
        "SELECT kind, finding_id, user_id, pass, frame, mjd, ra, dec FROM acc_taps WHERE kind IN ('recentre', 'find_again')"
    ):
        out[r["finding_id"]][r["kind"]].append(
            {"user": r["user_id"], "pass": r["pass"], "frame": r["frame"], "mjd": r["mjd"], "ra": r["ra"], "dec": r["dec"]}
        )
    return dict(out)


def _offsets(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float]:
    return ((ra - ra0 + 540.0) % 360.0 - 180.0) * math.cos(math.radians(dec0)) * 3600.0, (dec - dec0) * 3600.0


def fit(visits: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A constant motion through the visits: rate (″/h), position angle (degrees east of north), rms (″). None with fewer than 2."""
    pts = [v for v in visits if v.get("mjd") is not None]
    if len(pts) < 2:
        return None
    ra0, dec0 = float(pts[0]["ra"]), float(pts[0]["dec"])
    t0 = float(pts[0]["mjd"])
    ts = [(float(v["mjd"]) - t0) * 24.0 for v in pts]
    xy = [_offsets(float(v["ra"]), float(v["dec"]), ra0, dec0) for v in pts]
    n = len(pts)
    tm = sum(ts) / n
    stt = sum((t - tm) ** 2 for t in ts)
    if stt <= 0:
        return None
    vx = sum((t - tm) * x for t, (x, _) in zip(ts, xy, strict=True)) / stt
    vy = sum((t - tm) * y for t, (_, y) in zip(ts, xy, strict=True)) / stt
    xm, ym = sum(x for x, _ in xy) / n, sum(y for _, y in xy) / n
    res = [math.hypot(x - (xm + vx * (t - tm)), y - (ym + vy * (t - tm))) for t, (x, y) in zip(ts, xy, strict=True)]
    rms = math.sqrt(sum(r * r for r in res) / n)
    return {"rate": round(math.hypot(vx, vy), 3), "pa": round(math.degrees(math.atan2(vx, vy)) % 360.0, 1), "rms": round(rms, 2), "n": n}


def _agreed(taps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Places two or more different people tapped within AGREE_ARCSEC of each other (the median of the group)."""
    groups: list[list[dict[str, Any]]] = []
    for t in taps:
        for g in groups:
            if any(math.hypot(*_offsets(t["ra"], t["dec"], q["ra"], q["dec"])) <= AGREE_ARCSEC for q in g):
                g.append(t)
                break
        else:
            groups.append([t])
    out = []
    for g in groups:
        if len({t["user"] for t in g}) >= 2:
            mj = [t["mjd"] for t in g if t.get("mjd") is not None]
            out.append(
                {
                    "ra": median(t["ra"] for t in g),
                    "dec": median(t["dec"] for t in g),
                    "mjd": median(mj) if mj else None,
                    "people": len({t["user"] for t in g}),
                }
            )
    return out


def apply(find: dict[str, Any], said: dict[str, Any] | None) -> dict[str, Any]:
    """The find with data["people_track"] when people corrected its track (the find itself is not changed otherwise)."""
    if not said:
        return find
    data = find.get("data") or {}
    visits = data.get("visits")
    if not isinstance(visits, list) or not visits:
        return find
    missing = set(said.get("missing") or set())
    used = [dict(v, i=i) for i, v in enumerate(visits) if i not in missing]
    moved = 0
    for v in used:  # a visit two or more people re-centred takes the median of their taps
        near = [t for t in said.get("recentre") or [] if t.get("mjd") is not None and abs(float(t["mjd"]) - float(v["mjd"])) <= MATCH_DAYS]
        if len({t["user"] for t in near}) >= 2:
            v["ra"], v["dec"], v["recentred"] = median(t["ra"] for t in near), median(t["dec"] for t in near), True
            moved += 1
    base = fit(used)
    added: list[dict[str, Any]] = []
    for p in _agreed(said.get("find_again") or []):
        if p.get("mjd") is None or base is None:
            continue
        trial = fit([*used, *added, p])
        if trial is not None and trial["rms"] <= max(RMS_FLOOR, RMS_GROWTH * base["rms"]):
            added.append({**p, "found_again": True})
    if not missing and not moved and not added:
        return find
    final = fit([*used, *added])
    track: dict[str, Any] = {
        "missing": sorted(missing),
        "recentred": moved,
        "found_again": len(added),
        "visits": [{k: v[k] for k in ("ra", "dec", "mjd") if k in v} | ({"recentred": True} if v.get("recentred") else {}) for v in used]
        + [{"ra": a["ra"], "dec": a["dec"], "mjd": a["mjd"], "found_again": True} for a in added],
    }
    if final is None:
        track["state"] = "too few visits left"
    else:
        track.update(final, state="relinked")
    return {**find, "data": {**data, "people_track": track}}
