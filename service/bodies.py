"""Known solar-system bodies in a film: where JPL predicts them on every visit of the pass (the expected visits), so the page can ring
them on each frame and say on which visits the film's own motion map shows them.

    candidates  JPL Small-Body Identification (sb_ident) at the middle of the pass, over the field widened by how far a fast
                main-belt body moves in half the pass; V ≤ 20.5 (SPHEREx sees about V 20 in one visit)
    positions   JPL Horizons, geocentric, at the time of every frame (SPHEREx flies 700 km up: the parallax of a body 1 au away is
                about 1″, under a fifth of a detector pixel)
    expected    the frames whose grid holds the predicted position

Results are kept on disk per film; JPL answers are behind a breaker.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from engine.grid import Grid
from indexer.store import Index
from service.upstream import jpl
from service.upstream.breaker import Breaker

SB_IDENT = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
V_LIMIT = 20.5
MARGIN_RATE = 40.0  # ″/h: a fast main-belt body; the search window is widened by this rate over half the pass
MAX_BODIES = 200
# bodies JPL's Small-Body Identification does not list (interstellar comets): always asked of Horizons, kept when in the field
NOTABLE = ["C/2025 N1 (3I/ATLAS)"]
NEAR_DEG = 0.15  # a candidate whose straight-line track comes this close to the field is asked of Horizons


def _jd(mjd: float) -> float:
    return mjd + 2400000.5


def _iso(mjd: float) -> str:
    from datetime import UTC, datetime, timedelta

    t = datetime(1858, 11, 17, tzinfo=UTC) + timedelta(days=mjd)
    return t.strftime("%Y-%m-%d_%H:%M:%S")


def _sexa_ra(deg: float) -> str:
    h = (deg % 360.0) / 15.0
    hh = int(h)
    mm = int((h - hh) * 60)
    ss = ((h - hh) * 60 - mm) * 60
    return f"{hh:02d}-{mm:02d}-{ss:05.2f}"


def _sexa_dec(deg: float) -> str:
    sign = "-" if deg < 0 else ""
    d = abs(deg)
    dd = int(d)
    mm = int((d - dd) * 60)
    ss = ((d - dd) * 60 - mm) * 60
    return f"{sign}{dd:02d}-{mm:02d}-{ss:04.1f}"


def planes_holding(index: Index, mjd: np.ndarray, ra: np.ndarray, dec: np.ndarray) -> int:
    """Detector images of the index that hold a moving body: its ephemeris (mjd, ra, dec, a fine time step) interpolated to each
    exposure's mid-time and tested against that exposure's footprint (the count IRSA MOST calls planes)."""
    seen: set[int] = set()
    for k in range(len(mjd) - 1):
        for e in index.containing(float(ra[k]), float(dec[k]), float(mjd[k]), float(mjd[k + 1])):
            tm = 0.5 * (e.mjd0 + e.mjd1)
            if e.id in seen or not mjd[k] <= tm <= mjd[k + 1]:
                continue
            f = (tm - mjd[k]) / (mjd[k + 1] - mjd[k])
            r_t = float(ra[k] + f * (((ra[k + 1] - ra[k] + 540.0) % 360.0) - 180.0))
            d_t = float(dec[k] + f * (dec[k + 1] - dec[k]))
            if any(x.id == e.id for x in index.containing(r_t % 360.0, d_t, e.mjd0, e.mjd1)):
                seen.add(e.id)
    return len(seen)


def horizons_command(name: str) -> str:
    """'9078 (1994 PB2)' → '9078;'; '(2010 AB12)' or '2010 AB12' → 'DES=2010 AB12;'; a comet 'C/2025 N1 (ATLAS)' → 'DES=C/2025 N1;'."""
    m = re.match(r"^\s*(\d+)\s", name + " ")
    if m:
        return f"{m.group(1)};"
    m = re.search(r"\(([^)]+)\)", name)
    des = (m.group(1) if m and re.match(r"^\d{4}\s", m.group(1)) else name.split("(")[0]).strip()
    # a comet has one orbit per apparition: the one closest to now
    return f"DES={des};CAP;" if re.match(r"^[CPI]/", des) or re.match(r"^\d+[PI]/", des) else f"DES={des};"


class KnownBodies:
    def __init__(self, store_dir: Path, index: Index | None = None) -> None:
        self.index = index
        self.dir = store_dir / "bodies"
        self.breaker = Breaker("JPL")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), headers={"User-Agent": "spherex-sky/0.1"})
        self.running: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def cached(self, key: str) -> dict[str, Any] | None:
        p = self.dir / f"{key}.json"
        return json.loads(p.read_text()) if p.is_file() else None

    def start(self, key: str, meta: dict[str, Any]) -> None:
        if key not in self.running:
            self.running[key] = asyncio.get_running_loop().create_task(self._run(key, meta))

    async def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        self.breaker.before()
        try:
            r = await jpl.turn(lambda: self.client.get(url, params=params), "sbident" if url == SB_IDENT else "ssd")
            r.raise_for_status()
            body: dict[str, Any] = r.json()
        except Exception:
            self.breaker.failure()
            raise
        self.breaker.success()
        return body

    async def _horizons(self, params: dict[str, str]) -> dict[str, Any]:
        """Horizons without the breaker: one body's failure must not shut out the others."""
        r = await jpl.turn(lambda: self.client.get(HORIZONS, params=params))
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        if "result" not in body:
            raise ValueError(str(body)[:200])
        return body

    async def _run(self, key: str, meta: dict[str, Any]) -> dict[str, Any]:
        try:
            g = meta["grid"]
            grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
            frames = meta["frames"]
            mjds = [f["mjd"] for f in frames]
            mid = 0.5 * (min(mjds) + max(mjds))
            half_h = 12.0 * (max(mjds) - min(mjds))
            hw = min(3.0, g["n"] * g["step_arcsec"] / 3600.0 / 2.0 + MARGIN_RATE * half_h / 3600.0)
            found = await self._get(
                SB_IDENT,
                {
                    "mpc-code": "500",
                    "obs-time": _iso(mid),
                    "fov-ra-center": _sexa_ra(g["ra0"]),
                    "fov-dec-center": _sexa_dec(g["dec0"]),
                    "fov-ra-hwidth": f"{hw / max(0.05, math.cos(math.radians(g['dec0']))):.3f}",
                    "fov-dec-hwidth": f"{hw:.3f}",
                    "vmag-lim": str(V_LIMIT),
                    "two-pass": "true",
                    "suppress-first-pass": "true",
                },
            )
            rows = found.get("data_second_pass") or []
            fields = found.get("fields_second") or []
            iv = fields.index("Visual magnitude (V)") if "Visual magnitude (V)" in fields else 6
            idx = {name: fields.index(name) for name in fields}
            half = g["n"] * g["step_arcsec"] / 3600.0 / 2.0 + NEAR_DEG
            cands = []
            for row in rows:
                try:
                    dx0 = float(row[idx['Dist. from center RA (")']]) / 3600.0
                    dy0 = float(row[idx['Dist. from center Dec (")']]) / 3600.0
                    vx = float(row[idx['RA rate ("/h)']]) / 3600.0
                    vy = float(row[idx['Dec rate ("/h)']]) / 3600.0
                except (KeyError, ValueError):
                    continue
                # the straight-line track over the pass (offsets in degrees from the field centre; the rates are on the sky)
                hits = any(abs(dx0 + vx * (m - mid) * 24.0) <= half and abs(dy0 + vy * (m - mid) * 24.0) <= half for m in mjds)
                if hits:
                    cands.append(row)
            cands = sorted(cands, key=lambda r: float(r[iv]) if r[iv] not in ("", None) else 99.0)[:MAX_BODIES]
            tlist = " ".join(f"'{_jd(m):.6f}'" for m in mjds)
            sem = asyncio.Semaphore(3)

            async def ephemeris(row: list[Any]) -> dict[str, Any] | None:
                name = str(row[0])
                async with sem:
                    eph = None
                    for attempt in range(3):
                        try:
                            eph = await self._horizons(
                                {
                                    "format": "json",
                                    "COMMAND": f"'{horizons_command(name)}'",
                                    "CENTER": "'500@399'",
                                    "MAKE_EPHEM": "YES",
                                    "EPHEM_TYPE": "OBSERVER",
                                    "TLIST": tlist,
                                    "TLIST_TYPE": "JD",
                                    "QUANTITIES": "'1,9'",
                                    "ANG_FORMAT": "DEG",
                                    "CSV_FORMAT": "YES",
                                    "OBJ_DATA": "NO",
                                },
                            )
                            break
                        except Exception:  # JPL busy: a short pause and again; a body it never answers for is left out
                            await asyncio.sleep(2.0 * (attempt + 1))
                    if eph is None:
                        return None
                text = str(eph.get("result", ""))
                if "$$SOE" not in text:
                    return None
                lines = text.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines()
                pos = []
                for k, line in enumerate(lines[: len(mjds)]):
                    parts = [q.strip() for q in line.split(",")]
                    ra, dec = float(parts[3]), float(parts[4])
                    x, y = grid.sky_to_grid([ra], [dec])
                    xi, yi = float(x[0]), float(y[0])
                    inside = 0 <= xi < grid.n and 0 <= yi < grid.n
                    pos.append({"k": k, "ra": round(ra, 6), "dec": round(dec, 6), "x": round(xi, 2), "y": round(yi, 2), "inside": inside})
                expected = [q["k"] for q in pos if q["inside"]]
                if not expected:
                    return None
                v = float(row[iv]) if row[iv] else None
                if v is None:  # a body asked of Horizons directly: its apparent magnitude from the ephemeris
                    try:
                        v = float([q.strip() for q in lines[0].split(",")][5])
                    except (IndexError, ValueError):
                        v = None
                return {"name": name, "v": v, "frames": pos, "expected": expected}

            extra = [[name, "", "", "", "", "", "", "", ""] for name in NOTABLE]
            got = await asyncio.gather(*(ephemeris(r) for r in [*cands, *extra]))
            bodies = sorted([b for b in got if b is not None], key=lambda b: b["v"] if b["v"] is not None else 99.0)
            # how many SPHEREx detector images of the pass hold each body (all detectors; the count IRSA MOST gives)
            if self.index is not None:
                p0, p1 = float(meta["pass"]["mjd0"]) - 0.05, float(meta["pass"]["mjd1"]) + 0.05

                async def count(b: dict[str, Any]) -> None:
                    async with sem:
                        try:
                            eph = await self._horizons(
                                {
                                    "format": "json",
                                    "COMMAND": f"'{horizons_command(b['name'])}'",
                                    "CENTER": "'500@399'",
                                    "MAKE_EPHEM": "YES",
                                    "EPHEM_TYPE": "OBSERVER",
                                    "START_TIME": f"'JD {_jd(p0):.5f}'",
                                    "STOP_TIME": f"'JD {_jd(p1):.5f}'",
                                    "STEP_SIZE": "'30m'",
                                    "QUANTITIES": "'1'",
                                    "ANG_FORMAT": "DEG",
                                    "CSV_FORMAT": "YES",
                                    "OBJ_DATA": "NO",
                                    "CAL_FORMAT": "JD",
                                }
                            )
                        except Exception:  # the count stays unknown, never guessed
                            return
                    text = str(eph.get("result", ""))
                    if "$$SOE" not in text:
                        return
                    lines_ = text.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines()
                    rows_ = [[q.strip() for q in ln.split(",")] for ln in lines_]
                    m = np.array([float(x[0]) - 2400000.5 for x in rows_])
                    ra_ = np.array([float(x[3]) for x in rows_])
                    de_ = np.array([float(x[4]) for x in rows_])
                    assert self.index is not None
                    b["planes"] = await asyncio.to_thread(planes_holding, self.index, m, ra_, de_)

                await asyncio.gather(*(count(b) for b in bodies if (b["v"] or 99.0) <= 19.5))
            result = {
                "state": "done",
                "bodies": bodies,
                "candidates": len(cands),
                "in_window": len(rows),
                "v_limit": V_LIMIT,
                "source": "JPL sb_ident + Horizons",
            }
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{key}.part"
            tmp.write_text(json.dumps(result, separators=(",", ":")))
            tmp.replace(self.dir / f"{key}.json")
            return result
        except Exception as e:  # JPL not reachable: said, and asked again next time
            return {"state": "unavailable", "error": str(e)[:200], "bodies": []}
        finally:
            self.running.pop(key, None)

    def view(self, key: str) -> dict[str, Any]:
        hit = self.cached(key)
        if hit is not None:
            return hit
        t = self.running.get(key)
        if t is not None and t.done():
            return t.result()
        return {"state": "running" if t is not None else "idle", "bodies": []}
