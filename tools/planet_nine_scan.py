"""The Planet Nine searches of two one-degree fields, run by the service's own deep-sum job at low priority (night work on the production
machine, on a private instance of the service with its own store):

    the IRAS/AKARI candidate field (RA 34.72, Dec -49.79, where a far-infrared source pair of 1983 and 2006 points to a body at 500-700 au),
    detectors D1-D3, 250-2000 au, every bound orbit (the field lies 58 degrees south of the ecliptic, which no orbit inclined 30 degrees or
    less reaches, so the Planet Nine prior would leave nothing), and
    the field around RA 60.94, Dec +8.39 near the ecliptic (where an earlier search found one faint peak at 394 au), detector D4 alone
    (2.4-3.8 um, where a cold body is brightest in SPHEREx), 250-2000 au, only prograde orbits of 5-30 degrees inclination and 0.4-0.9 of
    the escape speed (the predicted Planet Nine).

Both use all the passes that cover the field. Nothing is copied from earlier work: the job reads the exposures and computes everything.

    uv run python tools/planet_nine_scan.py --base http://127.0.0.1:8790 --out planet_nine.json

The job is asked for with `bg=1`: it gives way to every person's job (a person's field always finds a free worker) but does not stop while
someone uses the site. The script asks again every minute, keeps the answer of each field in the output file as it comes, and says honestly
what each search found: the peaks above the noise ceiling of the negated sum, their photometric S/N, and the controls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PRIOR = "5,30,0.4,0.9"
FIELDS: list[dict[str, Any]] = [
    {"name": "IRAS/AKARI Planet Nine candidate field", "ra": 34.72, "dec": -49.79, "det": "123", "prior": None},
    {"name": "ecliptic field around RA 60.94 Dec +8.39", "ra": 60.94, "dec": 8.39, "det": "4", "prior": PRIOR},
]
DMIN, DMAX = 250.0, 2000.0


def passes_of(c: httpx.Client, ra: float, dec: float) -> list[float]:
    """The middle date of every pass that covers the place (from the exposure index, nothing is read)."""
    r = c.get("/api/v1/distant", params={"ra": ra, "dec": dec, "fov": 18, "dmin": 40, "dmax": 160, "det": "4", "plan": 1}, timeout=120)
    r.raise_for_status()
    return [0.5 * (p["mjd0"] + p["mjd1"]) for p in r.json().get("passes", [])]


def run_field(c: httpx.Client, f: dict[str, Any], poll_s: float, max_hours: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        **f,
        "prior": f["prior"],
        "det": f["det"],
        "dmin": f.get("dmin", DMIN),
        "dmax": f.get("dmax", DMAX),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    mids = passes_of(c, f["ra"], f["dec"])
    out["pass_mjd"] = [round(m, 2) for m in mids]
    if len(mids) < 2:
        out["state"] = "not-enough-passes"
        return out
    q: dict[str, Any] = {
        "fields": ";".join(f"{f['ra']},{f['dec']},{m:.3f}" for m in mids[:6]),
        "fov": f.get("fov", 60),
        "dmin": f.get("dmin", DMIN),
        "dmax": f.get("dmax", DMAX),
        "det": f["det"],
    }
    if f["prior"]:
        q["prior"] = f["prior"]
    plan = c.get("/api/v1/deep", params={**q, "plan": 1}, timeout=300).json()
    out["plan"] = {k: plan.get(k) for k in ("status", "estimate_s", "limit")} | {"hypotheses": (plan.get("plan") or {}).get("hypotheses")}
    if plan.get("status") != "plan":
        out["state"] = plan.get("status", "no-plan")
        return out
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_hours * 3600.0:
        try:
            r = c.get("/api/v1/deep", params={**q, "bg": 1}, timeout=300)
        except httpx.HTTPError:  # the service restarts (a deploy): ask again
            time.sleep(poll_s)
            continue
        if r.status_code >= 400:
            out["state"] = f"HTTP {r.status_code}"
            out["error"] = r.text[:200]
            return out
        body = r.json()
        out["phase"] = body.get("phase")
        if body.get("state") in ("done", "error", "cancelled", "failed"):
            out["state"] = body["state"]
            out["seconds"] = round(time.monotonic() - t0)
            res = body.get("result") or {}
            out["status"] = res.get("status")
            out["ceiling"], out["threshold"], out["sum_peak"] = res.get("ceiling"), res.get("threshold"), res.get("sum_peak")
            out["peaks"] = [
                {k: x.get(k) for k in ("sn", "sn_tuned", "phot_sn", "d", "dr", "ok", "sign")} for x in (res.get("candidates") or [])[:20]
            ]
            out["control"] = res.get("control")
            out["notes"] = res.get("notes")
            out["error"] = body.get("error")
            return out
        time.sleep(poll_s)
    out["state"] = "timeout"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8790")
    ap.add_argument("--out", default="planet_nine.json")
    ap.add_argument("--max-hours", type=float, default=6.0, help="per field")
    ap.add_argument("--poll-seconds", type=float, default=60.0)
    ap.add_argument("--only", type=int, default=None, help="search only this field (0 or 1)")
    a = ap.parse_args()
    out = Path(a.out)
    state: dict[str, Any] = json.loads(out.read_text()) if out.is_file() else {"fields": []}
    done = {d["name"] for d in state["fields"] if d.get("state") in ("done", "not-enough-passes", "too-many-hypotheses")}
    with httpx.Client(base_url=a.base, headers={"User-Agent": "planet-nine-scan/1"}) as c:
        for i, f in enumerate(FIELDS):
            if f["name"] in done or (a.only is not None and i != a.only):
                continue
            state["fields"] = [d for d in state["fields"] if d["name"] != f["name"]]
            state["fields"].append(run_field(c, f, a.poll_seconds, a.max_hours))
            out.write_text(json.dumps(state, indent=1))
            print(f["name"], state["fields"][-1].get("state"), state["fields"][-1].get("threshold"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
