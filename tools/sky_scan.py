"""The background sky scan: walk patches of the sky along the ecliptic and around the Planet Nine region, ask a running service to search
each one (movers and pairs between passes, brown dwarf candidates, the film search for unknown movers), and keep an honest count of what
was found.

It only calls the service's own routes, one patch at a time, so it works against a private instance on the production machine (run it
there at night, on a port of its own, with its own store) as well as against any other service:

    uv run python tools/sky_scan.py --base http://127.0.0.1:8790 --out scan.json [--fov 20] [--hours 10]

The output file is rewritten after every patch and the scan resumes from it, so it can be stopped and started again. Per patch it keeps
the state of every search, the number of leads, and the seconds each took; the totals at the top count patches searched, patches with
nothing found, and leads (moving objects, changing pairs, brown dwarf candidates, unknown movers of the film), never only the
interesting ones.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ECLIPTIC_STEP_DEG = 15.0  # a patch every 15 degrees of ecliptic longitude
PLANET_NINE = (34.72, -49.79)  # the IRAS/AKARI candidate field's centre; the patches lie on a ring around it
PLANET_NINE_RING_DEG = 6.0


def ecliptic_to_equatorial(lon: float, lat: float) -> tuple[float, float]:
    import math

    eps = math.radians(23.4392911)
    lo, la = math.radians(lon), math.radians(lat)
    ra = math.atan2(math.sin(lo) * math.cos(eps) - math.tan(la) * math.sin(eps), math.cos(lo))
    dec = math.asin(math.sin(la) * math.cos(eps) + math.cos(la) * math.sin(eps) * math.sin(lo))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def patches() -> list[dict[str, Any]]:
    import math

    out: list[dict[str, Any]] = []
    for k in range(int(360 / ECLIPTIC_STEP_DEG)):
        ra, dec = ecliptic_to_equatorial(k * ECLIPTIC_STEP_DEG, 0.0)
        out.append({"name": f"ecliptic {k * ECLIPTIC_STEP_DEG:.0f}°", "ra": round(ra, 4), "dec": round(dec, 4)})
    out.append({"name": "IRAS/AKARI field", "ra": PLANET_NINE[0], "dec": PLANET_NINE[1]})
    for k in range(8):
        a = math.radians(45.0 * k)
        dec = PLANET_NINE[1] + PLANET_NINE_RING_DEG * math.sin(a)
        ra = (PLANET_NINE[0] + PLANET_NINE_RING_DEG * math.cos(a) / max(math.cos(math.radians(dec)), 0.2)) % 360.0
        out.append({"name": f"Planet Nine region {45 * k}°", "ra": round(ra, 4), "dec": round(dec, 4)})
    return out


def ask(c: httpx.Client, path: str, params: dict[str, Any], until: set[str], limit_s: float, pause: float = 10.0) -> dict[str, Any]:
    """Ask a route again and again (asking keeps the service's job alive) until its state is in `until` or the time is out."""
    t0 = time.monotonic()
    body: dict[str, Any] = {}
    while time.monotonic() - t0 < limit_s:
        r = c.get(path, params=params, timeout=120)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "30")))
            continue
        if r.status_code >= 400:
            return {"state": "failed", "error": f"{path}: HTTP {r.status_code}"}
        body = r.json()
        if str(body.get("state")) in until:
            body["_seconds"] = round(time.monotonic() - t0, 1)
            return body
        time.sleep(pause)
    return {"state": "timeout", "_seconds": round(time.monotonic() - t0, 1), "last": str(body.get("phase", ""))}


def scan_patch(c: httpx.Client, p: dict[str, Any], fov: float, per_search_s: float) -> dict[str, Any]:
    q = {"ra": p["ra"], "dec": p["dec"], "fov": fov}
    res: dict[str, Any] = {**p, "fov": fov, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    field = ask(c, "/api/v1/fields", q, {"done"}, per_search_s)
    res["field"] = {"state": field.get("state"), "key": field.get("key"), "seconds": field.get("_seconds")}
    if field.get("state") != "done":
        return res
    movers = ask(c, "/api/v1/movers", q, {"done", "failed"}, per_search_s)
    body = movers.get("result") or {}
    res["movers"] = {
        "state": movers.get("state"),
        "tracks": len(body.get("tracks", [])),
        "pairs": len(movers.get("pairs", [])),
        "seconds": movers.get("_seconds"),
    }
    dwarfs = ask(c, "/api/v1/browndwarfs", q, {"done", "failed"}, per_search_s)
    res["browndwarfs"] = {
        "state": dwarfs.get("state"),
        "candidates": len((dwarfs.get("result") or {}).get("candidates", [])),
        "seconds": dwarfs.get("_seconds"),
    }
    meta = field.get("meta") or {}
    films = []
    for ps in meta.get("passes", [])[:1]:  # one pass per patch: the film search is the long one
        film = ask(c, "/api/v1/film", {**q, "mjd": ps["mjd_mid"]}, {"done"}, per_search_s)
        key = film.get("key")
        if film.get("state") == "done" and key:
            tr = ask(c, f"/api/v1/film/{key}/tracks", {}, {"done", "error"}, per_search_s)
            films.append(
                {"mjd": ps["mjd_mid"], "state": tr.get("state"), "tracks": len(tr.get("tracks", [])), "seconds": tr.get("_seconds")}
            )
        else:
            films.append({"mjd": ps["mjd_mid"], "state": film.get("state")})
    res["film"] = films
    return res


def leads(d: dict[str, Any]) -> int:
    return int(
        d.get("movers", {}).get("tracks", 0)
        + d.get("movers", {}).get("pairs", 0)
        + d.get("browndwarfs", {}).get("candidates", 0)
        + sum(f.get("tracks", 0) for f in d.get("film", []))
    )


def totals(done: list[dict[str, Any]]) -> dict[str, Any]:
    searched = [d for d in done if d.get("field", {}).get("state") == "done"]
    return {
        "patches_asked": len(done),
        "patches_searched": len(searched),
        "patches_with_nothing": sum(1 for d in searched if leads(d) == 0),
        "leads": sum(leads(d) for d in searched),
        "failed_or_timed_out": len(done) - len(searched),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8790", help="the service to ask")
    ap.add_argument("--out", default="sky_scan.json")
    ap.add_argument("--fov", type=float, default=20.0, help="patch size in arcminutes (the film search wants 20 or less)")
    ap.add_argument("--hours", type=float, default=10.0, help="stop starting new patches after this long")
    ap.add_argument("--per-search-minutes", type=float, default=40.0)
    a = ap.parse_args()
    out = Path(a.out)
    state: dict[str, Any] = json.loads(out.read_text()) if out.is_file() else {"patches": []}
    have = {(d["ra"], d["dec"]) for d in state["patches"]}
    t_end = time.monotonic() + a.hours * 3600.0
    with httpx.Client(base_url=a.base, headers={"User-Agent": "sky-scan/1"}) as c:
        for p in patches():
            if (p["ra"], p["dec"]) in have:
                continue
            if time.monotonic() > t_end:
                break
            try:
                state["patches"].append(scan_patch(c, p, a.fov, a.per_search_minutes * 60.0))
            except httpx.HTTPError as e:  # the service did not answer: said, and the next patch is tried
                state["patches"].append({**p, "fov": a.fov, "field": {"state": "failed", "error": str(e)[:200]}})
            state["totals"] = totals(state["patches"])
            out.write_text(json.dumps(state, indent=1))
            print(p["name"], state["patches"][-1].get("movers"), state["totals"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
