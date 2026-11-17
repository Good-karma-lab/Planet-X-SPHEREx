"""People's queue for the brown-dwarf search (spec §5g; service/accounts/agreement.brown_dwarf_queue).

A red source that people call a cold brown dwarf, or a source people marked that no catalogue has, goes to `acc_bd_queue`. This
module looks at them one at a time, as the warm runner's work (P2, never while a person's job waits): a 5′ field centred on the
source is built, the brown-dwarf search of that field is run, and whether it found a candidate at the source is kept. A candidate
it finds is a find like any other (the findings collector reads the search's result files), so it appears in Findings by itself.

What was looked at is kept in <store>/people/bd_queue.json: {finding_id: {field, state: checked | failed, found, at}}.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from service.jobs.scheduler import Priority
from service.products.store import product_key
from service.workers import fieldjob

BD_FOV = 5.0
NEAR_ARCSEC = 6.0  # a candidate of the search this close to the queued source is that source


def _sep_arcsec(ra: float, dec: float, ra0: float, dec0: float) -> float:
    return math.hypot(((ra - ra0 + 540.0) % 360.0 - 180.0) * math.cos(math.radians(dec0)), dec - dec0) * 3600.0


class BDQueue:
    def __init__(self, st: Any) -> None:
        self.st = st
        self.path = Path(st.cfg.store_dir) / "people" / "bd_queue.json"
        self.done: dict[str, dict[str, Any]] = {}
        try:
            self.done = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.done = {}
        self.current: dict[str, Any] | None = None  # the entry being looked at: {finding_id, ra, dec, field}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".part")
            tmp.write_text(json.dumps(self.done, separators=(",", ":")))
            tmp.replace(self.path)
        except OSError:
            pass

    def queue(self) -> list[dict[str, Any]]:
        """The queued sources not looked at yet, the oldest first (a database read: call it in a thread)."""
        from service import accounts
        from service.accounts import agreement

        try:
            db = accounts.get(self.st).db
            rows = agreement.get(self.st, db).brown_dwarf_queue()
        except Exception:  # no accounts here: nothing is queued
            return []
        return [r for r in rows if r.get("finding_id") and r["finding_id"] not in self.done and r.get("ra") is not None]

    def field_of(self, ra: float, dec: float) -> tuple[str, dict[str, Any]] | None:
        index = getattr(self.st, "index", None)
        if index is None:
            return None
        params = fieldjob.canonical(float(ra), float(dec), BD_FOV)
        return product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(index, params),)), params

    def step(self, waiting: list[dict[str, Any]]) -> str | None:
        """One step, on the scheduler's loop: start the field of the next source, or its brown-dwarf search once the field is built, or
        keep what the search found. Returns what happened, for /health."""
        sch, store, dwarfs = getattr(self.st, "scheduler", None), getattr(self.st, "store", None), getattr(self.st, "dwarfs", None)
        submit = getattr(self.st, "submit_field", None)
        if sch is None or store is None or dwarfs is None or submit is None or sch.person_waiting():
            return None
        if self.current is None:
            nxt = next(iter(waiting), None)
            if nxt is None:
                return None
            got = self.field_of(nxt["ra"], nxt["dec"])
            if got is None:
                return None
            self.current = {
                "finding_id": nxt["finding_id"],
                "ra": float(nxt["ra"]),
                "dec": float(nxt["dec"]),
                "field": got[0],
                "params": got[1],
            }
        cur = self.current
        if not store.is_complete(cur["field"]):
            try:
                submit(cur["params"], Priority.P2)
            except Exception as e:  # a standby, or no exposure here: said, and the source is not looked at again
                return self._finish(cur, "failed", False, f"{type(e).__name__}: {e}"[:160])
            return f"building {cur['finding_id']}"
        run = dwarfs.get_or_start(cur["field"])
        if run.state == "running":
            return f"searching {cur['finding_id']}"
        if run.state == "failed":
            return self._finish(cur, "failed", False, run.error[:160])
        cands = (run.result or {}).get("candidates", [])
        found = any(_sep_arcsec(float(c["ra"]), float(c["dec"]), cur["ra"], cur["dec"]) <= NEAR_ARCSEC for c in cands if "ra" in c)
        return self._finish(cur, "checked", found, "")

    def _finish(self, cur: dict[str, Any], state: str, found: bool, why: str) -> str:
        self.done[cur["finding_id"]] = {
            "field": cur["field"],
            "state": state,
            "found": found,
            "at": round(time.time()),
            **({"why": why} if why else {}),
        }
        self._save()
        self.current = None
        return f"{state} {cur['finding_id']}{' (found)' if found else ''}"
