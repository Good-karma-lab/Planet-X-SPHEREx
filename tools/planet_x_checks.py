"""The Find Planet X checks behind the numbers of the Planet X page, run on the production service at the lowest priority once its warm
set is complete (every person's request goes first):

    sedna   the field of Sedna (V 20.8, about 83 au), searched blind: the deep sum of its passes along every orbit at 75-92 au, D1-D3;
            the proof that the search finds a known body this faint
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
    "sedna": {"name": "Sedna, blind", "ra": 61.7725, "dec": 8.8408, "det": "123", "prior": None, "fov": 18, "dmin": 75.0, "dmax": 92.0},
    "p9": {"name": "ecliptic field of the 394 au peak, Planet Nine prior", "ra": 60.94, "dec": 8.39, "det": "4", "prior": PRIOR},
}


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
            out[k] = run_field(c, dict(f), a.poll_seconds, a.max_hours)
            Path(a.out).write_text(json.dumps(out, indent=1))
            print(time.strftime("%H:%M:%S"), "done", f["name"], json.dumps(out[k])[:400], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
