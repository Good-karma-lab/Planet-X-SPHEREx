"""The Find Planet X checks behind the numbers of the Planet X page, run on the production service at the lowest priority once its warm
set is complete (every person's request goes first):

    sedna   Sedna (V 20.8, about 83 au), searched blind: from its place at the middle pass (JPL), the service places the field of
            every other pass for 75-92 au by itself and sums the passes along every orbit there, D1-D3; the proof that the search
            finds a known body this faint
    p9      the field around RA 60.94, Dec +8.39 where a one-pass peak at about 394 au was seen, searched again with the Planet Nine
            prior (prograde, 5-30 degrees inclination, 0.4-0.9 of the escape speed), D4, 250-2000 au, one degree

    /opt/sky/.venv/bin/python tools/planet_x_checks.py --base http://127.0.0.1:8780 --out /tmp/planet_x_checks.json [--only sedna]

Each answer is written to the output file as it comes (peaks, their photometric S/N, the noise ceiling of the negated sum, controls).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from planet_nine_scan import PRIOR, run_field

CHECKS = {
    # Sedna's place at the middle pass (JPL Horizons, MJD 61091.52) and the middle dates of the three passes that cover it
    "sedna": {
        "name": "Sedna, blind",
        "centre": "60.71543,8.63254,61091.52",
        "passes": "60915.848;61091.52;61274.472",
        "det": "123",
        "prior": None,
        "fov": 18,
        "dmin": 75.0,
        "dmax": 92.0,
    },
    "p9": {"name": "ecliptic field of the 394 au peak, Planet Nine prior", "ra": 60.94, "dec": 8.39, "det": "4", "prior": PRIOR},
}


def run_centre(c: httpx.Client, f: dict, poll_s: float, max_hours: float) -> dict:
    """The deep sum from a place and the dates of the passes: the service places every field for the distance range."""
    q = {"centre": f["centre"], "passes": f["passes"], "fov": f["fov"], "dmin": f["dmin"], "dmax": f["dmax"], "det": f["det"]}
    out: dict = {**f, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    plan = c.get("/api/v1/deep", params={**q, "plan": 1}).json()
    out["plan"] = {k: plan.get(k) for k in ("status", "estimate_s", "limit", "fields")} | {
        "hypotheses": (plan.get("plan") or {}).get("hypotheses")
    }
    if plan.get("status") != "plan":
        out["state"] = plan.get("status", "no-plan")
        return out
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_hours * 3600.0:
        r = c.get("/api/v1/deep", params={**q, "background": 1})
        if r.status_code >= 400:
            return {**out, "state": f"HTTP {r.status_code}", "error": r.text[:200]}
        body = r.json()
        if body.get("state") in ("done", "error", "cancelled", "failed"):
            res = body.get("result") or {}
            keys = ("version", "status", "ceiling", "threshold", "sum_peak", "control", "notes", "candidates", "hypotheses")
            return {**out, "state": body["state"], "seconds": round(time.monotonic() - t0), "error": body.get("error")} | {
                k: res.get(k) for k in keys
            }
        time.sleep(poll_s)
    return {**out, "state": "timeout"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8780")
    ap.add_argument("--out", default="planet_x_checks.json")
    ap.add_argument("--only", choices=sorted(CHECKS), default=None)
    ap.add_argument("--max-hours", type=float, default=4.0)
    ap.add_argument("--poll-seconds", type=float, default=60.0)
    a = ap.parse_args()
    out: dict[str, object] = {}
    with httpx.Client(base_url=a.base, timeout=300) as c:
        for k, f in CHECKS.items():
            if a.only and k != a.only:
                continue
            print(time.strftime("%H:%M:%S"), "start", f["name"], flush=True)
            out[k] = (
                run_centre(c, dict(f), a.poll_seconds, a.max_hours) if "centre" in f else run_field(c, dict(f), a.poll_seconds, a.max_hours)
            )
            Path(a.out).write_text(json.dumps(out, indent=1))
            print(time.strftime("%H:%M:%S"), "done", f["name"], json.dumps(out[k])[:400], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
