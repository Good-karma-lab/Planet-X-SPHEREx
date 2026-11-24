"""Where a find's task opens: on a field that is built, so that a person sees what the question is about at once.

A find is a place in a searched square. The sweep strips the pictures of most squares after searching them, so a task that opened at
10′ on the find itself built a field for minutes before the first picture (24 Nov 2026). A task opens instead on the smallest built
field that holds the place (the service's own word: complete, not stripped, of today's exposures), with the find at its own place in
it. A find in no built field is asked about last, and its field (the 10′ quarter of its square, else 10′ on the find) is built ahead,
one at a time, while the disk has room."""

from __future__ import annotations

import math
import shutil
import threading
import time
from collections.abc import Iterable
from typing import Any

from service.products.store import product_key
from service.workers import fieldjob

MARGIN_ARCMIN = 0.7  # the place is at least this far inside the field
MAX_FOV = 20.0  # on a wider field a find is a speck
LIST_TTL_S = 30.0  # the store's fields are listed again after this
STALE_TTL_S = 600.0  # a field's exposures are asked of the index again after this
DISK_STOP_PCT = 72.0  # nothing is built ahead on a disk this full (the keeper cleans above 75)


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
        self._fresh: dict[str, tuple[float, bool]] = {}  # product key -> (asked at, built of today's exposures and not stripped)

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
        if getattr(self.st, "store", None) is None or getattr(self.st, "index", None) is None:
            return None
        near = [(f, _inside(ra, dec, f)) for f in self._listing()]
        good = [x for x in near if x[1] >= MARGIN_ARCMIN]
        for f, _ in sorted(good, key=lambda x: (float(x[0]["fov"]) < 10.0, float(x[0]["fov"]), -x[1])):
            if self._usable(f):
                return {k: float(f[k]) for k in ("ra", "dec", "fov")}
        return None

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
        return 100.0 * u.used / u.total < DISK_STOP_PCT

    def spectrum_kept(self, anomaly: dict[str, Any]) -> bool:
        """The odd pictures of an anomaly are on disk: its spectrum is measured, its stamps kept."""
        sp = getattr(self.st, "spectra", None)
        if sp is None:
            return False
        key = sp.key_of(anomaly_params(anomaly))
        return bool((sp.dir / f"{key}.json").is_file() and (sp.dir / f"{key}.stamps.json").is_file())


def anomaly_params(a: dict[str, Any]) -> dict[str, Any]:
    """The spectrum the lab asks for of an anomaly (web: useSpectrum on the anomaly's own place and motion)."""
    from service.spectrum import spectrum_params

    return spectrum_params(round(float(a["ra"]), 6), round(float(a["dec"]), 6), float(a.get("pmra") or 0.0), float(a.get("pmdec") or 0.0))
