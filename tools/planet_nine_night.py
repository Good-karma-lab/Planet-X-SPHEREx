"""The overnight Find Planet X runs on the two Planet Nine fields, through the running service at low priority (every person's job
goes first), so their real results appear on the searched layer and in Findings.

    /opt/sky/.venv/bin/python tools/planet_nine_night.py --base http://127.0.0.1:8780

Fields: the IRAS/AKARI Planet Nine candidate field and the ecliptic field around RA 60.94°, Dec +8.39° (the place of a 6.1σ
one-pass peak at about 394 au in Aug 2025). Each: the field is built (if not yet), then the deep sum of its passes along every orbit
between DMIN and DMAX au is asked for with bg=1 and followed until it ends; a log line per step goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

FIELDS = [
    {"name": "IRAS/AKARI Planet Nine field", "ra": 34.72, "dec": -49.79},
    {"name": "ecliptic field of the 394 au peak", "ra": 60.94, "dec": 8.39},
]
FOV = 18.0
DMIN, DMAX = 250.0, 800.0


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def wait_field(c: httpx.Client, base: str, ra: float, dec: float) -> dict:
    while True:
        r = c.get(f"{base}/api/v1/fields", params={"ra": ra, "dec": dec, "fov": 20})
        if r.status_code == 200:
            b = r.json()
            if b.get("state") == "done":
                return b["meta"]
            log("field:", (b.get("job") or {}).get("phase"))
        time.sleep(30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8780")
    a = ap.parse_args()
    c = httpx.Client(timeout=120)
    for f in FIELDS:
        meta = wait_field(c, a.base, f["ra"], f["dec"])
        mjds = [p["mjd_mid"] for p in meta["passes"]]
        if len(mjds) < 2:
            log(f["name"], ": fewer than two passes, skipped")
            continue
        params = {
            "centre": f"{f['ra']},{f['dec']},{mjds[0]}",
            "passes": ";".join(f"{m:.4f}" for m in mjds),
            "fov": FOV,
            "dmin": DMIN,
            "dmax": DMAX,
            "bg": 1,
        }
        plan = c.get(f"{a.base}/api/v1/deep", params={**params, "plan": 1}).json()
        fields = list(plan.get("fields") or [])
        # a pass with too few visits for the sum sits out (the deep sum needs two passes or more)
        err = str((plan.get("plan") or {}).get("error", ""))
        if plan.get("status") == "few-visits" and err.startswith("field "):
            bad = int(err.split()[1].rstrip(":")) - 1
            fields = [x for i, x in enumerate(fields) if i != bad]
            log(f["name"], f"pass {bad + 1} sits out ({err})")
        if len(fields) < 2:
            log(f["name"], ": fewer than two usable passes, skipped")
            continue
        job = {k: v for k, v in params.items() if k not in ("centre", "passes")}
        job["fields"] = ";".join(f"{x[0]},{x[1]},{x[2]}" for x in fields)
        p2 = c.get(f"{a.base}/api/v1/deep", params={**job, "plan": 1}).json()
        log(f["name"], "plan:", json.dumps({k: p2.get(k) for k in ("status", "estimate_s", "orbits") if k in p2}))
        while True:
            r = c.get(f"{a.base}/api/v1/deep", params=job)
            b2 = r.json() if r.status_code == 200 else {"state": f"http {r.status_code}"}
            state = b2.get("state") or b2.get("status")
            log(f["name"], state, (b2.get("job") or {}).get("phase", "") if isinstance(b2.get("job"), dict) else "")
            if state not in ("running", "queued", "waiting", "paused", None) or b2.get("result"):
                log(f["name"], "result:", json.dumps(b2.get("result") or b2)[:2000])
                break
            time.sleep(60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
