"""The lab's automatic Find Planet X searches of the five test targets, computed once on the production service at background priority,
so that a visitor's lab finds them stored (the same requests the lab makes: the middle pass, 30-300 au and 250-2000 au, D1-D3 and D4, and
the two deep sums over every pass). The lab searches at most 18′ (its 20′ and 60′ fields ask the same 18′ search) and never below 5′.

    /opt/sky/.venv/bin/python tools/warm_fpx.py --base http://127.0.0.1:8780 [--sizes 5,10,20,60]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.warm import WarmField, warm_set

TARGETS = ("Barnard's Star", "ε Indi B", "WISE 0855−0714", "RW Aurigae", "R Draconis")
SEARCHES = [(30, 300, None), (30, 300, "4"), (250, 2000, "123"), (250, 2000, "4")]
DEEP = [(60, 110), (250, 800)]  # the lab's deep sums (plan=1 asks first; bg=1 runs them)


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ask(c: httpx.Client, path: str, params: dict[str, Any]) -> Any:
    for _ in range(30):
        try:
            r = c.get(path, params=params)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(10)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8780")
    ap.add_argument("--sizes", default="5,10,20,60")
    ap.add_argument(
        "--places",
        default="",
        help="'ra,dec[,fov][;...]': these fields, 5′ unless a size is given (the Planet X lead links) instead of the targets",
    )
    a = ap.parse_args()
    sizes = {float(x) for x in a.sizes.split(",")}
    if a.places:
        fields = [
            WarmField(f"place {p}", float(q[0]), float(q[1]), float(q[2]) if len(q) > 2 else 5.0)
            for p in a.places.split(";")
            if p.strip()
            for q in [p.split(",")]
        ]
    else:
        fields = [f for f in warm_set() if f.film_mjd is None and f.label in TARGETS and f.fov in sizes and f.fov >= 5]
    seen: set[tuple[str, float]] = set()
    with httpx.Client(base_url=a.base, timeout=600) as c:
        for f in fields:
            if (f.label, min(f.fov, 18)) in seen:
                continue  # 20′ and 60′ ask the same 18′ search
            seen.add((f.label, min(f.fov, 18)))
            body = ask(c, "/api/v1/fields", {"ra": f.ra, "dec": f.dec, "fov": f.fov})
            passes = ((body or {}).get("meta") or {}).get("passes") or []
            if not passes:
                log("no field meta", f.label, f.fov)
                continue
            mjd = passes[len(passes) // 2]["mjd_mid"]
            for dmin, dmax, det in SEARCHES:
                q: dict[str, Any] = {"ra": f.ra, "dec": f.dec, "fov": min(f.fov, 18), "dmin": dmin, "dmax": dmax, "bg": 1, "mjd": mjd}
                if det:
                    q["det"] = det
                t0 = time.monotonic()
                while True:
                    b = ask(c, "/api/v1/distant", q) or {}
                    if b.get("state") in ("done", "error") or (b.get("result") and not b.get("job")):
                        break
                    if time.monotonic() - t0 > 3600:
                        break
                    time.sleep(30)
                log(f.label, f"{f.fov:g}′", f"{dmin}-{dmax} au", det or "123", b.get("state"), f"{time.monotonic() - t0:.0f} s")
            if len(passes) >= 3:  # the lab's two deep sums over every pass: a plan places the fields, the run uses them (as the lab)
                every = ";".join(str(p["mjd_mid"]) for p in passes)
                for dmin, dmax in DEEP:
                    pq = {
                        "centre": f"{f.ra:.5f},{f.dec:.5f},{passes[0]['mjd_mid']}",
                        "passes": every,
                        "fov": min(f.fov, 18),
                        "dmin": dmin,
                        "dmax": dmax,
                        "plan": 1,
                        "bg": 1,
                    }
                    plan = ask(c, "/api/v1/deep", pq) or {}
                    if plan.get("status") != "plan" or not plan.get("fields"):
                        log(f.label, f"{f.fov:g}′", f"deep {dmin}-{dmax} au", "no plan", plan.get("status"))
                        continue
                    fields_q = ";".join(f"{float(x[0]):.5f},{float(x[1]):.5f},{x[2]}" for x in plan["fields"])
                    rq = {"fields": fields_q, "fov": min(f.fov, 18), "dmin": dmin, "dmax": dmax, "bg": 1}
                    t0 = time.monotonic()
                    while True:
                        b = ask(c, "/api/v1/deep", rq) or {}
                        if b.get("state") in ("done", "error", "cancelled") or (b.get("result") and not b.get("job")):
                            break
                        if time.monotonic() - t0 > 5400:
                            break
                        time.sleep(30)
                    log(f.label, f"{f.fov:g}′", f"deep {dmin}-{dmax} au", b.get("state"), f"{time.monotonic() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
