"""Expected visits of known asteroids against IRSA MOST (validation of service/bodies.py's plane count).

For four main-belt bodies in the ecliptic field on 14-20 Dec 2025, MOST lists 119, 107, 118 and 121 SPHEREx planes (detector images)
that hold the body. Here: JPL Horizons gives the body every 30 minutes; every exposure of the index whose footprint holds the body at
its own mid-time (interpolated) is counted.
    uv run python validation/v_most.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indexer.store import Index
from service.bodies import planes_holding

MOST = {"9952": 119, "43959": 107, "9078": 118, "11020": 121}
T0, T1 = "2025-12-14 00:00", "2025-12-21 00:00"


def ephemeris(body: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = httpx.get(
        "https://ssd.jpl.nasa.gov/api/horizons.api",
        params={
            "format": "json",
            "COMMAND": f"'{body};'",
            "CENTER": "'500@399'",
            "MAKE_EPHEM": "YES",
            "EPHEM_TYPE": "OBSERVER",
            "START_TIME": f"'{T0}'",
            "STOP_TIME": f"'{T1}'",
            "STEP_SIZE": "'30m'",
            "QUANTITIES": "'1'",
            "ANG_FORMAT": "DEG",
            "CSV_FORMAT": "YES",
            "OBJ_DATA": "NO",
            "TIME_TYPE": "UT",
            "CAL_FORMAT": "JD",
        },
        timeout=60,
    )
    text = r.json()["result"].split("$$SOE")[1].split("$$EOE")[0]
    rows = [[q.strip() for q in line.split(",")] for line in text.strip().splitlines()]
    jd = np.array([float(x[0]) for x in rows])
    return jd - 2400000.5, np.array([float(x[3]) for x in rows]), np.array([float(x[4]) for x in rows])


def main() -> int:
    index = Index(Path("store/index/exposures.sqlite"), readonly=True)
    print("| body | MOST planes | index planes |\n|---|---|---|")
    for body, most in MOST.items():
        mjd, ra, dec = ephemeris(body)
        print(f"| {body} | {most} | {planes_holding(index, mjd, ra, dec)} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
