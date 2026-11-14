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

from engine.grid import Grid
from service.upstream.breaker import Breaker

SB_IDENT = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
V_LIMIT = 20.5
MARGIN_RATE = 40.0  # ″/h: a fast main-belt body; the search window is widened by this rate over half the pass
MAX_BODIES = 200
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


def horizons_command(name: str) -> str:
    """'9078 (1994 PB2)' → '9078;'; '(2010 AB12)' or '2010 AB12' → 'DES=2010 AB12;'; a comet 'C/2025 N1 (ATLAS)' → 'DES=C/2025 N1;'."""
    m = re.match(r"^\s*(\d+)\s", name + " ")
    if m:
        return f"{m.group(1)};"
    m = re.search(r"\(([^)]+)\)", name)
    des = (m.group(1) if m and re.match(r"^\d{4}\s", m.group(1)) else name.split("(")[0]).strip()
    return f"DES={des};"


class KnownBodies:
    def __init__(self, store_dir: Path) -> None:
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
            r = await self.client.get(url, params=params)
            r.raise_for_status()
            body: dict[str, Any] = r.json()
        except Exception:
            self.breaker.failure()
            raise
        self.breaker.success()
        return body

    async def _horizons(self, params: dict[str, str]) -> dict[str, Any]:
        """Horizons without the breaker: one body's failure must not shut out the others."""
        r = await self.client.get(HORIZONS, params=params)
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
                return {"name": name, "v": float(row[iv]) if row[iv] else None, "frames": pos, "expected": expected}

            got = await asyncio.gather(*(ephemeris(r) for r in cands))
            bodies = sorted([b for b in got if b is not None], key=lambda b: b["v"] if b["v"] is not None else 99.0)
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
