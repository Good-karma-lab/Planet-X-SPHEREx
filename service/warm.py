"""The warm runner: the warm set is computed before anyone asks, at the lowest priority but one (P2), one job at a time, and pauses
while a person is active (docs/plan/05-architecture.md 5.6). It is part of the service process, so no client can pose as it.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class WarmField:
    label: str
    ra: float
    dec: float
    fov: float
    film_mjd: float | None = None  # a film of the pass at this date instead of a field


def place_at_epoch(t: dict[str, Any], epoch: float) -> tuple[float, float]:
    """A catalogue place (J2000, proper motion in mas/yr) moved to `epoch` (parallax is below the rounding of the address)."""
    dt = epoch - float(t.get("epoch", 2000.0))
    dec = float(t["dec_deg"]) + float(t["pmdec_mas_yr"]) * dt / 3.6e6
    ra = float(t["ra_deg"]) + float(t["pmra_mas_yr"]) * dt / 3.6e6 / math.cos(math.radians(0.5 * (dec + float(t["dec_deg"]))))
    return ra % 360.0, dec


def warm_set(data: Path = DATA) -> list[WarmField]:
    w = json.loads((data / "warm.json").read_text(encoding="utf-8"))
    targets = {t["label"]: t for t in json.loads((data / "targets.json").read_text(encoding="utf-8"))["targets"]}
    out = []
    for name in w["targets"]:
        ra, dec = place_at_epoch(targets[name], float(w["epoch"]))
        out += [WarmField(name, ra, dec, float(s)) for s in w["sizes"]]
    for d in w["demo_fields"]:
        out += [WarmField(d["label"], float(d["ra"]), float(d["dec"]), float(s)) for s in d["sizes"]]
    # the sizes a visitor opens first come first for every target; the slow 60′ fields last
    rank = {5.0: 0, 10.0: 1, 20.0: 2, 2.0: 3, 60.0: 4}
    out = sorted(out, key=lambda f: rank.get(f.fov, 5))
    out += [WarmField(f["label"], float(f["ra"]), float(f["dec"]), float(f["fov"]), float(f["mjd"])) for f in w.get("films", [])]
    return out


@dataclass
class WarmRunner:
    """Calls `ensure(field)` for each warm field in turn; `ensure` returns True when the product is complete (now or after waiting)."""

    fields: list[WarmField]
    done: set[WarmField] = field(default_factory=set)
    failed: dict[WarmField, str] = field(default_factory=dict)
    state: str = "idle"

    async def run(self, ensure: Any, complete: Any = None) -> None:
        """`complete(field)` (cheap, no computing) marks every field already built before anything is built, so a restart resumes at
        the first field still missing instead of counting from zero behind it."""
        self.state = "running"
        if complete is not None:
            for f in self.fields:
                if f not in self.done:
                    try:
                        if await complete(f):
                            self.done.add(f)
                    except Exception:  # the check is retried by ensure() below
                        pass
        for f in self.fields:
            if f in self.done:
                continue
            try:
                if await ensure(f):
                    self.done.add(f)
                    self.failed.pop(f, None)
            except Exception as e:  # reported in /health, retried on the next run
                self.failed[f] = f"{type(e).__name__}: {e}"
            await asyncio.sleep(0)
        self.state = "done" if len(self.done) == len(self.fields) else "incomplete"

    def snapshot(self) -> dict[str, Any]:
        missing = [f"{f.label} {f.fov:g}′" for f in self.fields if f not in self.done]
        return {
            "state": self.state,
            "done": len(self.done),
            "total": len(self.fields),
            "missing": missing[:8],
            "failed": {f"{k.label} {k.fov:g}′": v for k, v in list(self.failed.items())[:4]},
        }
