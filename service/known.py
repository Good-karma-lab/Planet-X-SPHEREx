"""Known small bodies for the globe: the JPL catalogue (SBDB), one object by name, its geocentric ephemeris (Horizons) and the places
where its path crosses
SPHEREx exposures of the index.

Every answer is JPL's: nothing is made up and a JPL that does not answer is an error (`JplUnavailable`), never an empty list. Answers
are kept in memory and on
disk (catalogue pages one day, ephemerides and objects one week); the Horizons ephemeris of a body is also asked for the whole mission
to find its crossings.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import numpy as np
from scipy.interpolate import CubicSpline

from indexer.store import Index
from service.storage import digest
from service.upstream import jpl
from service.upstream.breaker import Breaker, BreakerOpenError

SBDB_QUERY = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"
SBDB_OBJECT = "https://ssd-api.jpl.nasa.gov/sbdb.api"
HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
SPHEREX_CENTER = "500@-163182"  # Horizons: the SPHEREx spacecraft, the observer of every exposure
SBDB_FIELDS = ["spkid", "full_name", "pdes", "name", "kind", "class", "H", "diameter", "albedo", "a", "e", "i", "q"]
SORTS = {"number": "spkid", "H": "H", "diameter": "-diameter", "name": "full_name"}
DAY = 86400.0
TLIST_MAX = 80  # Horizons answers the first 80 epochs of a TLIST
MISSION_START = "2025-04-01"
VISIT_GAP_DAYS = 0.6  # exposures of one body closer than this in time are one crossing
CROSS_STEP = "3h"
FIELD_ARCMIN = 12.0
SB_ALIASES = {"oumuamua": "1I", "'oumuamua": "1I", "1i/oumuamua": "1I", "1i/'oumuamua": "1I"}
QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "ʼ": "'", "`": "'"}
DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}
COMET = re.compile(r"^(\d+[PDCXI]|[PCDXI]/)")


class JplUnavailable(RuntimeError):  # noqa: N818
    """JPL did not answer (or answered something that is not an answer): the page says so."""


def normalize_name(q: str) -> str:
    q = unicodedata.normalize("NFKC", q or "")
    for a, b in [*QUOTES.items(), *DASHES.items()]:
        q = q.replace(a, b)
    return re.sub(r"\s+", " ", q.replace(" ", " ")).strip()[:80]


def query_clean(q: str) -> str:
    """A small-body name as SBDB wants it: no parentheses (`(433) Eros` -> `433 Eros`, a trailing `(provisional)` dropped), a few
    aliases."""
    t = normalize_name(q)
    m = re.match(r"^(.+?\S)\s*\([^)]*\)$", t)
    if m and not t.startswith("("):
        t = m.group(1)
    t = re.sub(r"\s+", " ", t.replace("(", " ").replace(")", " ")).strip()
    return SB_ALIASES.get(t.lower(), t)


def candidates(lst: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for x in lst[:12]:
        pdes, name = str(x.get("pdes") or ""), str(x.get("name") or x.get("pdes") or "")
        is_comet = bool(re.match(r"^(\d+[PDCXI]|[CPDXI]/)", pdes) or re.match(r"^[CPDXI]/", name))
        out.append({"pdes": pdes, "full_name": name, "kind": "comet" if is_comet else "asteroid"})
    return out


def pick(q: str, lst: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one record of an SBDB list that the query names (full name or designation exactly, or its leading number/designation); None
    when it cannot be told."""
    ql = q.strip().lower()

    def nm(x: dict[str, Any]) -> str:
        return str(x.get("name") or "").lower()

    def pd(x: dict[str, Any]) -> str:
        return str(x.get("pdes") or "").lower()

    def base(x: dict[str, Any]) -> str:
        return re.sub(r"\s*\([^)]*\)\s*$", "", nm(x)).strip()

    tok = re.split(r"[ /]", ql)[0]
    for hit in (
        [x for x in lst if ql in (nm(x), pd(x), base(x))],
        [x for x in lst if tok == pd(x)] if re.search(r"\d", tok) else [],
    ):
        if len(hit) == 1:
            return hit[0]
    return None


def horizons_command(target: str) -> str:
    """Horizons COMMAND of a target: comets by designation with the current apparition, numbered asteroids by number, an SPK id as it is."""
    t = target.strip()
    if COMET.match(t):
        des = t.split(" ")[0].split("/")[0] if re.match(r"^\d+[PDCXI]", t) else t
        return f"DES={des};CAP;NOFRAG;"
    if t.isdigit():
        return f"{t};"
    if re.match(r"^\d{4} [A-Z]{2}\d*$", t):  # a provisional designation (2024 YR4), not a number and a name
        return f"DES={t};"
    m = re.match(r"^(\d+)\s+[A-Za-z]", t)
    return f"{m.group(1)};" if m else t


def horizons_rows(result: str) -> list[dict[str, Any]]:
    """Rows of an OBSERVER ephemeris with QUANTITIES 1, 9, 20: date, ra, dec, V (APmag or T-mag), delta_au."""
    if "$$SOE" not in result:
        raise JplUnavailable("Horizons: " + result[:200].replace("\n", " "))
    out = []
    for line in result.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines():
        c = [x.strip() for x in line.split(",")]
        try:
            out.append(
                {
                    "date": c[0],
                    "ra": float(c[3]),
                    "dec": float(c[4]),
                    "V": float(c[5]) if c[5] not in ("", "n.a.") else None,
                    "delta_au": float(c[7]) if len(c) > 7 and c[7] else None,
                }
            )
        except (ValueError, IndexError):
            continue
    return out


def jd_of_mjd(mjd: float) -> float:
    return mjd + 2400000.5


def mjd_of_date(d: datetime) -> float:
    return (d - datetime(1858, 11, 17, tzinfo=UTC)).total_seconds() / DAY


def index_mjd_range(index: Index) -> list[float] | None:
    """[first mjd0, last mjd1] of the exposures of the index (health: `index.mjd_range`), None when it is empty."""
    row = index.db.execute("SELECT MIN(mjd0), MAX(mjd1) FROM files").fetchone()
    if row is None or row[0] is None:
        return None
    return [round(float(row[0]), 4), round(float(row[1]), 4)]


def unit(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    a, d = np.radians(ra), np.radians(dec)
    return np.stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)], axis=-1)


def radec(v: np.ndarray) -> tuple[float, float]:
    v = v / np.linalg.norm(v)
    return float(np.degrees(np.arctan2(v[1], v[0])) % 360.0), float(np.degrees(np.arcsin(max(-1.0, min(1.0, v[2])))))


def group_crossings(hits: list[tuple[float, float]], gap: float = VISIT_GAP_DAYS) -> list[tuple[float, float]]:
    """Exposures (mjd0, mjd1) that hold the body -> the visits: (first mjd0, last mjd1) of the runs whose exposures are less than `gap`
    days apart."""
    out: list[list[float]] = []
    for m0, m1 in sorted(hits):
        if out and m0 - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], m1)
        else:
            out.append([m0, m1])
    return [(a, b) for a, b in out]


class Known:
    def __init__(self, store_dir: Path, index: Index | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.index = index
        self.dir = store_dir / "known"
        self.breaker = Breaker("JPL small bodies")
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), headers={"User-Agent": "spherex-sky/0.1"})
        self.memory: dict[str, tuple[float, Any]] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    # ---- plumbing -----------------------------------------------------------------------------------------------------------
    async def _get(
        self, url: str, params: dict[str, str] | None = None, data: dict[str, str] | None = None, ok: tuple[int, ...] = (200,)
    ) -> Any:
        try:
            self.breaker.before()
        except BreakerOpenError as e:
            raise JplUnavailable(str(e)) from e
        try:
            r = await jpl.turn(lambda: self.client.post(url, data=data) if data is not None else self.client.get(url, params=params))
            if r.status_code not in ok:
                r.raise_for_status()
            body = r.json()
        except Exception as e:
            self.breaker.failure()
            raise JplUnavailable(f"JPL did not answer: {type(e).__name__} {str(e)[:100]}") from e
        self.breaker.success()
        return body

    async def _kept(self, name: str, ttl: float, fetch: Any) -> Any:
        key = digest(name)[:24]
        hit = self.memory.get(key)
        if hit is not None and time.time() - hit[0] < ttl:
            return hit[1]
        path = self.dir / f"{key}.json"
        if path.is_file() and time.time() - path.stat().st_mtime < ttl:
            val = json.loads(path.read_text())
            self.memory[key] = (time.time(), val)
            return val
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            val = await fetch()
            self.memory[key] = (time.time(), val)
            if len(self.memory) > 2000:
                self.memory.pop(next(iter(self.memory)))
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_text(json.dumps(val, separators=(",", ":")))
            tmp.replace(path)
            return val

    # ---- the catalogue ------------------------------------------------------------------------------------------------------
    async def page(self, kind: str, page: int, per: int, sort: str, cls: str) -> dict[str, Any]:
        """One page of the JPL SBDB catalogue (asteroids and comets), kept a day."""
        params = {"fields": ",".join(SBDB_FIELDS), "limit": str(per), "limit-from": str(page * per), "full-prec": "false"}
        if kind in ("a", "c"):
            params["sb-kind"] = kind
        if cls:
            params["sb-class"] = cls
        if sort in SORTS:
            params["sort"] = SORTS[sort]
        url = SBDB_QUERY + "?" + "&".join(f"{k}={quote(v, safe=',-')}" for k, v in params.items())

        async def fetch() -> dict[str, Any]:
            d = await self._get(url)
            rows = [dict(zip(d["fields"], r, strict=True)) for r in d.get("data", [])]
            for r in rows:
                r["full_name"] = (r.get("full_name") or "").strip()
            return {"count": int(d.get("count", 0)), "page": page, "per": per, "rows": rows, "source": "JPL SBDB Query API"}

        return await self._kept("page|" + url, DAY, fetch)  # type: ignore[no-any-return]

    async def _object(self, q: str) -> dict[str, Any]:
        async def fetch() -> dict[str, Any]:
            return await self._get(SBDB_OBJECT, params={"phys-par": "1", "sstr": q}, ok=(200, 300))  # type: ignore[no-any-return]

        d: dict[str, Any] = await self._kept("object|" + q.lower(), 7 * DAY, fetch)
        return d

    async def lookup(self, q: str) -> dict[str, Any]:
        """One small body by any name: the SBDB record as a one-row page, or `candidates` when the name matches several that cannot be
        told apart."""
        q = query_clean(q)
        d = await self._object(q)
        lst = d.get("list") or []
        if "object" not in d and lst:
            p = pick(q, lst)
            if p and p.get("pdes"):
                d2 = await self._object(str(p["pdes"]))
                if "object" in d2:
                    d = d2
        if "object" not in d:
            return {
                "count": 0,
                "rows": [],
                "list": [x.get("name") for x in d.get("list", [])][:20],
                "candidates": candidates(d.get("list", [])),
            }
        o, phys = d["object"], {x["name"]: x.get("value") for x in d.get("phys_par", [])}
        orb = {x["name"]: x.get("value") for x in d.get("orbit", {}).get("elements", [])}
        row = {
            "spkid": o.get("spkid"),
            "full_name": o.get("fullname"),
            "pdes": o.get("des"),
            "kind": o.get("kind"),
            "class": (o.get("orbit_class") or {}).get("code"),
            "H": phys.get("H"),
            "diameter": phys.get("diameter"),
            "albedo": phys.get("albedo"),
            "a": orb.get("a"),
            "e": orb.get("e"),
            "i": orb.get("i"),
            "q": orb.get("q"),
        }
        return {"count": 1, "rows": [row], "source": "JPL SBDB API"}

    # ---- ephemerides --------------------------------------------------------------------------------------------------------
    async def ephem(self, target: str, jds: list[float], observer: str = "500") -> list[dict[str, Any]]:
        """Astrometric RA/Dec (deg), V and distance of a body at the given JDs (UTC), one row per JD in the order given, from Horizons
        (kept a week)."""
        keys = [round(float(x), 6) for x in jds[:200]]
        uniq = sorted(set(keys))
        comet = bool(COMET.match(target.strip()))
        by: dict[float, dict[str, Any]] = {}
        for i in range(0, len(uniq), TLIST_MAX):
            part = uniq[i : i + TLIST_MAX]
            params = {
                "format": "json",
                "COMMAND": f"'{horizons_command(target)}'",
                "OBJ_DATA": "'NO'",
                "MAKE_EPHEM": "'YES'",
                "EPHEM_TYPE": "'OBSERVER'",
                "CENTER": f"'{observer}'",
                "TLIST": ",".join(f"'{x:.6f}'" for x in part),
                "QUANTITIES": "'1,9,20'",
                "ANG_FORMAT": "'DEG'",
                "CSV_FORMAT": "'YES'",
            }

            async def fetch(params: dict[str, str] = params) -> list[dict[str, Any]]:
                res = (await self._get(HORIZONS, data=params))["result"]
                rows = horizons_rows(res)
                for r in rows:
                    r["mag_kind"] = "T-mag (total, model)" if comet else "V (H,G model)"
                return rows

            rows = await self._kept("ephem|" + json.dumps(params, sort_keys=True), 7 * DAY, fetch)
            by.update(zip(part, rows, strict=False))
        return [by[k] for k in keys if k in by]

    # ---- crossings ----------------------------------------------------------------------------------------------------------
    async def path(self, target: str, mjd0: float, mjd1: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The geocentric path of a body, one point every 3 hours: (mjd, ra, dec) — kept a day (the end of the mission moves)."""
        params = {
            "format": "json",
            "COMMAND": f"'{horizons_command(target)}'",
            "OBJ_DATA": "'NO'",
            "MAKE_EPHEM": "'YES'",
            "EPHEM_TYPE": "'OBSERVER'",
            "CENTER": "'500'",
            "START_TIME": f"'JD {jd_of_mjd(mjd0):.4f}'",
            "STOP_TIME": f"'JD {jd_of_mjd(mjd1):.4f}'",
            "STEP_SIZE": f"'{CROSS_STEP}'",
            "QUANTITIES": "'1'",
            "ANG_FORMAT": "'DEG'",
            "CSV_FORMAT": "'YES'",
            "CAL_FORMAT": "'JD'",
        }

        async def fetch() -> list[list[float]]:
            res = (await self._get(HORIZONS, params=params))["result"]
            if "$$SOE" not in res:
                raise JplUnavailable("Horizons: " + res[:200].replace("\n", " "))
            rows = []
            for line in res.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines():
                c = [x.strip() for x in line.split(",")]
                try:
                    rows.append([float(c[0]) - 2400000.5, float(c[3]), float(c[4])])
                except (ValueError, IndexError):
                    continue
            return rows

        rows = await self._kept("path|" + json.dumps(params, sort_keys=True), DAY, fetch)
        a = np.array(rows, float).reshape(-1, 3)
        return a[:, 0], a[:, 1], a[:, 2]

    def _cross(self, m: np.ndarray, ra: np.ndarray, dec: np.ndarray) -> list[dict[str, Any]]:
        assert self.index is not None
        if len(m) < 4:
            return []
        v = unit(ra, dec)
        sp = CubicSpline(m, v, axis=0)
        hits: set[int] = set()
        meta: dict[int, tuple[float, float]] = {}
        for k in range(len(m) - 1):
            for e in self.index.containing(float(ra[k]), float(dec[k]), float(m[k]), float(m[k + 1])):
                tm = 0.5 * (e.mjd0 + e.mjd1)
                if e.id in hits or not m[k] <= tm <= m[k + 1]:
                    continue
                r_t, d_t = radec(sp(tm))
                if any(x.id == e.id for x in self.index.containing(r_t, d_t, e.mjd0, e.mjd1)):
                    hits.add(e.id)
                    meta[e.id] = (e.mjd0, e.mjd1)
        out = []
        for g0, g1 in group_crossings(list(meta.values())):
            mid = 0.5 * (g0 + g1)
            r_mid, d_mid = radec(sp(min(max(mid, float(m[0])), float(m[-1]))))
            near = self.index.overlapping(r_mid, d_mid, FIELD_ARCMIN / 60.0 / 2.0 * math.sqrt(2.0))
            n = sum(1 for e in near if g0 - 1e-6 <= e.mjd0 and e.mjd1 <= g1 + 1e-6)
            out.append({"ra": round(r_mid, 5), "dec": round(d_mid, 5), "mjd0": round(g0, 4), "mjd1": round(g1, 4), "n": n})
        return out

    async def crossings(self, target: str) -> dict[str, Any]:
        """Where the body's path crosses exposures of the index, one entry per visit (ra, dec at mid visit, the visit's span, the
        exposures of a 12′ field)."""
        if self.index is None:
            raise JplUnavailable("the exposure index is not loaded")
        rng = index_mjd_range(self.index)
        if rng is None:
            return {"crossings": [], "note": "the index is empty"}
        start = max(rng[0] - 1.0, mjd_of_date(datetime.fromisoformat(MISSION_START).replace(tzinfo=UTC)))
        stop = rng[1] + 1.0
        m, ra, dec = await self.path(target, start, stop)
        found = await asyncio.to_thread(self._cross, m, ra, dec)
        return {
            "target": target,
            "crossings": found,
            "source": "JPL Horizons path, geocentric, every 3 hours, against the exposure index",
            "mjd_range": rng,
        }
