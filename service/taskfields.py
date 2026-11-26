"""Where a find's task opens: on a field that is built, so that a person sees what the question is about at once.

A find is a place in a searched square. The sweep strips the pictures of most squares after searching them, so a task that opened at
10′ on the find itself built a field for minutes before the first picture (24 Nov 2026). A task opens instead on the smallest built
field that holds the place (the service's own word: complete, not stripped, of today's exposures), with the find at its own place in
it. A find in no built field is asked about last, and its field (the 10′ quarter of its square, else 10′ on the find) is built ahead,
one at a time, while the disk has room."""

from __future__ import annotations

import json
import math
import shutil
import threading
import time
from collections.abc import Iterable
from typing import Any

from service.products.store import product_key
from service.storage import atomic_write_bytes
from service.workers import fieldjob

MARGIN_ARCMIN = 0.7  # the place is at least this far inside the field
MAX_FOV = 20.0  # on a wider field a find is a speck
LIST_TTL_S = 30.0  # the store's fields are listed again after this
STALE_TTL_S = 600.0  # a field's exposures are asked of the index again after this
RETRY_S = 2 * 3600.0  # a build (or a spectrum) asked for is not asked for again for this long: one that fails must not stand in the way
PINNED_FILE = "task_fields.json"  # what this module pinned (the fields others pinned are theirs)
DISK_STOP_PCT = 77.0  # nothing is built ahead on a disk this full (the keeper cleans above 75, once an hour)
FAST_PCT = 3.0  # nor, above FAST_FROM_PCT, while the disk grew by more than this in FAST_S: people's fields and the sweep fill it then
FAST_FROM_PCT = 72.0
FAST_S = 1800.0
# an anomaly is asked about only when the star in the middle of its odd pictures is seen: in the stamps the task shows (web
# taskviews StampsView), the middle stands at least STAMP_SN times the stamp's own noise above its sky, in half of them (the median).
# The exploratory report of 27 Nov 2026 (С2): anomaly:bdb2bb574761 was asked about over six stamps of noise (median 10.1); the
# neighbour of HD 21496 that people judged at once stands 55-164
STAMP_SN = 20.0
STAMPS_SHOWN = 6  # web taskviews MAX_STAMPS


def _inside(ra: float, dec: float, p: dict[str, Any]) -> float:
    """How far inside a field a place is, arcminutes (negative: outside)."""
    dy = abs(dec - float(p["dec"])) * 60.0
    dx = abs((ra - float(p["ra"]) + 540.0) % 360.0 - 180.0) * math.cos(math.radians(dec)) * 60.0
    return float(p["fov"]) / 2.0 - max(dx, dy)


class TaskFields:
    def __init__(self, st: Any) -> None:
        self.st = st
        self._lock = threading.Lock()
        self._fields: list[dict[str, Any]] = []
        self._at = -1e9
        self._used: list[tuple[float, float]] = []  # how full the disk was, over the last FAST_S
        self._tried: dict[str, float] = {}  # what was asked to be made, and when
        self._fresh: dict[str, tuple[float, bool]] = {}  # product key -> (asked at, built of today's exposures and not stripped)
        self._star: dict[str, bool] = {}  # spectrum key -> its odd pictures show the star (star_in_stamps), measured once

    def _listing(self) -> list[dict[str, Any]]:
        with self._lock:
            if time.monotonic() - self._at > LIST_TTL_S:
                self._fields = [
                    {**x["params"], "key": x["key"]}
                    for x in self.st.store.listing((fieldjob.KIND,))
                    if x["complete"] and float(x["params"].get("fov", 99.0)) <= MAX_FOV
                ]
                self._at = time.monotonic()
            return self._fields

    def _usable(self, f: dict[str, Any]) -> bool:
        hit = self._fresh.get(f["key"])
        now = time.monotonic()
        if hit is not None and now - hit[0] < STALE_TTL_S:
            return hit[1]
        params = {k: f[k] for k in ("ra", "dec", "fov")}
        ok = (
            fieldjob.canonical(params["ra"], params["dec"], params["fov"]) == params  # the lab's address opens this very field
            and product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(self.st.index, params),)) == f["key"]
            and not fieldjob.unread(self.st.store.read_meta(fieldjob.KIND, f["key"]))
        )
        self._fresh[f["key"]] = (now, ok)
        return ok

    def place(self, ra: float, dec: float) -> dict[str, Any] | None:
        """The smallest built field that holds a place (10′ before 20′; under 5′ the sky around is lost), or None."""
        f = self._best(ra, dec)
        return None if f is None else {k: float(f[k]) for k in ("ra", "dec", "fov")}

    def _best(self, ra: float, dec: float) -> dict[str, Any] | None:
        if getattr(self.st, "store", None) is None or getattr(self.st, "index", None) is None:
            return None
        near = [(f, _inside(ra, dec, f)) for f in self._listing()]
        good = [x for x in near if x[1] >= MARGIN_ARCMIN]
        for f, _ in sorted(good, key=lambda x: (float(x[0]["fov"]) < 10.0, float(x[0]["fov"]), -x[1])):
            if self._usable(f):
                return f
        return None

    def first_untried(self, names: list[str], now: float | None = None) -> int | None:
        """The place in the list of the first thing not asked for in the last RETRY_S, marked as asked for now; None when all were."""
        t = time.monotonic() if now is None else now
        for i, n in enumerate(names):
            if t - self._tried.get(n, -1e9) >= RETRY_S:
                self._tried[n] = t
                return i
        return None

    def pin(self, finds: Iterable[dict[str, Any]]) -> tuple[int, int]:
        """Keeps the fields the finds' tasks open on (neither the sweep strips a pinned field nor the disk keeper deletes one), and lets
        go of those this module pinned that no task opens on any more. (pinned now, let go of)"""
        store = self.st.store
        path = store.root / PINNED_FILE
        try:
            mine = {str(k) for k in json.loads(path.read_text())}
        except (OSError, ValueError):
            mine = set()
        want = {f["key"] for r in finds if (f := self._best(float(r["ra"]), float(r["dec"]))) is not None}
        new = {k for k in want - mine if not store.is_pinned(k)}
        gone = mine - want
        if not new and not gone:
            return 0, 0
        # the list is written first: a pin it does not know would stay for ever
        atomic_write_bytes(path, json.dumps(sorted((mine | new) - gone)).encode())
        for k in new:
            store.pin(k)
        for k in gone:
            store.pin(k, False)
        return len(new), len(gone)

    def wanted(self, finds: Iterable[dict[str, Any]], quarter_at: Any = None) -> list[dict[str, Any]]:
        """The fields to build, in the finds' order: the quarter of the find's square when it holds the find, else 10′ on the find."""
        out: list[dict[str, Any]] = []
        for r in finds:
            ra, dec = float(r["ra"]), float(r["dec"])
            if self.place(ra, dec) is not None:
                continue
            p = None
            if quarter_at is not None:
                q = quarter_at(ra, dec)
                if q is not None and q.get("ra") is not None and _inside(ra, dec, {**q, "fov": 10.0}) >= MARGIN_ARCMIN:
                    p = fieldjob.canonical(float(q["ra"]), float(q["dec"]), 10.0)
            p = p or fieldjob.canonical(ra, dec, 10.0)
            if p not in out:
                out.append(p)
        return out

    def room(self) -> bool:
        """The disk has room to build ahead."""
        try:
            u = shutil.disk_usage(self.st.store.root)
        except (OSError, AttributeError):
            return False
        used = 100.0 * u.used / u.total
        now = time.monotonic()
        self._used = [(t, p) for t, p in self._used if now - t <= FAST_S] + [(now, used)]
        if used >= FAST_FROM_PCT and used - min(p for _, p in self._used) > FAST_PCT:
            return False
        return used < DISK_STOP_PCT

    def spectrum_kept(self, anomaly: dict[str, Any]) -> bool:
        """The odd pictures of an anomaly are on disk: its spectrum is measured, its stamps kept."""
        sp = getattr(self.st, "spectra", None)
        if sp is None:
            return False
        key = sp.key_of(anomaly_params(anomaly))
        return bool((sp.dir / f"{key}.json").is_file() and (sp.dir / f"{key}.stamps.json").is_file())

    def star_in_stamps(self, anomaly: dict[str, Any]) -> bool | None:
        """Whether the odd pictures the task shows of an anomaly show its star (STAMP_SN); None while they are not on disk."""
        sp = getattr(self.st, "spectra", None)
        if sp is None:
            return None
        key = sp.key_of(anomaly_params(anomaly))
        if key in self._star:
            return self._star[key]
        try:
            points = json.loads((sp.dir / f"{key}.json").read_text())["points"]
            stamps = json.loads((sp.dir / f"{key}.stamps.json").read_text())
        except (OSError, ValueError, KeyError):
            return None
        shown = shown_stamps(points, stamps, anomaly.get("drivers") or [])
        sns = [s for p in shown if (s := stamp_sn(stamps[str(p["i"])]["raw"])) is not None]
        self._star[key] = bool(sns) and _median(sns) >= STAMP_SN
        return self._star[key]


def _median(v: list[float]) -> float:
    s, n = sorted(v), len(v)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def shown_stamps(points: list[dict[str, Any]], stamps: dict[str, Any], drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The odd pictures a task shows of an anomaly, as web taskviews StampsView picks them: the measured exposures inside its odd
    bins that have a stamp, the farthest from the spectrum's median flux first, at most STAMPS_SHOWN."""
    ok = [p for p in points if p.get("ok") and p.get("flux") is not None and p.get("lam") is not None]
    inside = [p for p in ok if any(float(d["lo"]) <= p["lam"] <= float(d["hi"]) for d in drivers)]
    fl = sorted(float(p["flux"]) for p in ok)
    med = fl[len(fl) // 2] if fl else 0.0
    have = [p for p in inside if (stamps.get(str(p["i"])) or {}).get("raw")]
    return sorted(have, key=lambda p: -abs(float(p["flux"]) - med))[:STAMPS_SHOWN]


def stamp_sn(raw: list[list[float | None]]) -> float | None:
    """How far the middle of a stamp stands above its own sky: (the brightest pixel within 1.5 px of the centre - the median of the
    ring 4-8 px) / the ring's MAD sigma. None for a stamp without room for the ring."""
    import numpy as np

    a = np.array([[np.nan if v is None else v for v in row] for row in raw], dtype=float)
    if a.ndim != 2:
        return None
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - (w - 1) / 2.0, yy - (h - 1) / 2.0)
    ring = a[(d >= 4.0) & (d <= 8.0) & np.isfinite(a)]
    mid = a[(d <= 1.5) & np.isfinite(a)]
    if ring.size < 20 or mid.size == 0:
        return None
    med = float(np.median(ring))
    sig = 1.4826 * float(np.median(np.abs(ring - med)))
    return (float(mid.max()) - med) / sig if sig > 0 else None


def anomaly_params(a: dict[str, Any]) -> dict[str, Any]:
    """The spectrum the lab asks for of an anomaly (web: useSpectrum on the anomaly's own place and motion)."""
    from service.spectrum import spectrum_params

    return spectrum_params(round(float(a["ra"]), 6), round(float(a["dec"]), 6), float(a.get("pmra") or 0.0), float(a.get("pmdec") or 0.0))
