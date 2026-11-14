"""Pairs that change in place for a finished field (docs/plan/02-methods/2.5-searches.md): runs `engine.pairs` on the stacks the field
already published (no new reads from the
archive), names both components from SIMBAD and Gaia, and keeps the answer as JSON next to the other searches.

States of a run: running -> done | failed. The answer is persisted only when every component was identified (or the catalogue said
'none'); a component whose catalogue did not
answer stays 'unavailable' and is asked again on the next request after `CHECK_AGAIN_S`.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from engine import pairs as epairs
from engine.grid import Grid
from service import cpu
from service.context import CatalogueContext
from service.products.store import Store
from service.products.sxp import decode
from service.storage import digest
from service.workers import fieldjob

PAIRS_VERSION = "pairs-2"
CHECK_AGAIN_S = 60.0
IDENT_RADIUS_ARCSEC = 4.0
IDENT_EPOCH = 2025.9
SIMBAD_KEYS = ("state", "name", "otype", "sep", "mag", "link")
GAIA_KEYS = ("state", "source_id", "g", "bp_rp", "parallax", "pmra", "pmdec", "sep", "variable")


@dataclass
class PairRun:
    key: str
    field_key: str
    state: str = "running"  # running | done | failed
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    checked_at: float = 0.0


def unavailable(ident: dict[str, Any] | None) -> bool:
    return ident is None or any(ident.get(k, {}).get("state") == "unavailable" for k in ("simbad", "gaia"))


def short(d: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    d = d or {"state": "unavailable"}
    out = {k: d[k] for k in keys if k in d}
    if "source_id" in out and out["source_id"] is not None:
        out["source_id"] = str(out["source_id"])
    return out


class Pairs:
    def __init__(self, store: Store, store_dir: Path, catalogue: CatalogueContext | None = None) -> None:
        self.store = store
        self.dir = store_dir / "pairs"
        self.catalogue = catalogue
        self.runs: OrderedDict[str, PairRun] = OrderedDict()

    def key_of(self, field_key: str) -> str:
        return digest("pairs", PAIRS_VERSION, field_key)

    def _ctx(self) -> CatalogueContext:
        if self.catalogue is None:
            self.catalogue = CatalogueContext()
        return self.catalogue

    def get_or_start(self, field_key: str) -> PairRun:
        key = self.key_of(field_key)
        run = self.runs.get(key)
        if run is None:
            path = self.dir / f"{key}.json"
            if path.is_file():
                run = PairRun(key, field_key, state="done", result=json.loads(path.read_text()))
            else:
                run = PairRun(key, field_key)
                asyncio.get_running_loop().create_task(self._run(run))
            self.runs[key] = run
            while len(self.runs) > 200:
                self.runs.popitem(last=False)
        elif run.state == "done" and run.result.get("identified") is False and time.monotonic() - run.checked_at > CHECK_AGAIN_S:
            run.checked_at = time.monotonic()
            asyncio.get_running_loop().create_task(self._identify_all(run))
        return run

    async def _run(self, run: PairRun) -> None:
        try:
            meta = self.store.read_meta(fieldjob.KIND, run.field_key)
            if meta is None:
                raise ValueError("the field is not finished")
            # reading in a thread, the fits in a worker process (never in the API process)
            stacks = await asyncio.to_thread(self.stacks, run.field_key, meta)
            step = float(meta["grid"]["step_arcsec"])
            analysed = await cpu.run(epairs.analyse, *stacks, step)
            run.result = self.search(run.field_key, meta, analysed, stacks)
            run.checked_at = time.monotonic()
            await self._identify_all(run)
            run.state = "done"
        except Exception as e:  # say why; the next request tries again
            run.state, run.error = "failed", str(e)[:300]
            self.runs.pop(run.key, None)

    def stacks(
        self, field_key: str, meta: dict[str, Any]
    ) -> tuple[list[NDArray[np.float32]], list[NDArray[np.float32]], list[dict[str, NDArray[np.float32]]]]:
        """Per pass: the white stack (mean of the detectors the white frame uses, where all have data), the saturated share and the
        detector stacks D1..D6 (the ones with data)."""
        cdet = [int(d) for d in meta["cdet"]]
        whites: list[NDArray[np.float32]] = []
        sats: list[NDArray[np.float32]] = []
        dets: list[dict[str, NDArray[np.float32]]] = []
        for p in meta["passes"]:
            k = p["index"]
            raw_d = self.store.read_part(fieldjob.KIND, field_key, f"dets_p{k}.sxp")
            raw_s = self.store.read_part(fieldjob.KIND, field_key, f"picture_p{k}.sxp")
            if raw_d is None or raw_s is None:
                raise ValueError("a pass of the field is missing")
            st = decode(raw_d)[1]["dets"].astype(np.float32)
            sat = decode(raw_s)[1]["sat"].astype(np.float32) / 255.0
            use = st[[d - 1 for d in cdet]]
            ok = np.isfinite(use).all(axis=0)
            whites.append(np.where(ok, np.nan_to_num(use).mean(axis=0), np.nan).astype(np.float32))
            sats.append(sat)
            dets.append({f"D{d + 1}": st[d] for d in range(st.shape[0]) if np.isfinite(st[d]).mean() > 0.5})
        return whites, sats, dets

    def search(self, field_key: str, meta: dict[str, Any], res: Any = None, stacks: Any = None) -> dict[str, Any]:
        t0 = time.monotonic()
        g = meta["grid"]
        grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
        step = float(g["step_arcsec"])
        whites, sats, dets = stacks if stacks is not None else self.stacks(field_key, meta)
        if res is None:
            res = epairs.analyse(whites, sats, dets, step)
        finds = []
        for f in res["finds"]:
            comps = []
            for k, pq in enumerate((f["pa"], f["pb"])):
                ra, dec = grid.grid_to_sky([pq[0]], [pq[1]])
                fw = f["flux_white"]
                comps.append(
                    {
                        "ra": round(float(ra[0]), 6),
                        "dec": round(float(dec[0]), 6),
                        "xy": [round(pq[0], 2), round(pq[1], 2)],
                        "flux_per_pass": [None if r is None else r[k] for r in fw],
                        "flux_per_pass_err": [None if r is None else r[2 + k] for r in fw],
                        "flux_per_detector": {nm: [None if r is None else r[k] for r in rows] for nm, rows in f["flux_detector"].items()},
                        "snr": f["snr"][k],
                        "simbad": {"state": "pending"},
                        "gaia": {"state": "pending"},
                    }
                )
            finds.append(
                {
                    "kind": "pair",
                    "separation_arcsec": f["separation_arcsec"],
                    "components": comps,
                    "ratio_per_pass": f["ratio"],
                    "cov_per_pass": [None if r is None else r[4] for r in f["flux_white"]],
                    "dln_ratio_per_detector": f["dln_ratio"],
                    "verdict": f["verdict"],
                    "spread": f["spread"],
                    "mean_change": f["mean_change"],
                    "change_sigma": f["change_sigma"],
                    "crops": f["crops"],
                    "note": (
                        "ratio_per_pass = component 2 / component 1 (white stack); "
                        "dln_ratio_per_detector = ln of that ratio in each pass against the first, per detector"
                    ),
                }
            )
        return {
            "field": field_key,
            "mjd": [round(float(p["mjd_mid"]), 4) for p in meta["passes"]],
            "pairs": finds,
            "chance_pairs": res["chance_pairs"],
            "sources": res["sources"],
            "close_pairs": res["close_pairs"],
            "identified": not finds,
            "seconds": round(time.monotonic() - t0, 2),
        }

    async def _identify(self, comp: dict[str, Any]) -> None:
        ctx = await self._ctx().context(comp["ra"], comp["dec"], IDENT_EPOCH, IDENT_RADIUS_ARCSEC)
        comp["simbad"] = short(ctx.get("simbad"), SIMBAD_KEYS)
        comp["gaia"] = short(ctx.get("gaia"), GAIA_KEYS)

    async def _identify_all(self, run: PairRun) -> None:
        for rec in run.result.get("pairs", []):
            for c in rec["components"]:
                if c["simbad"].get("state") in ("pending", "unavailable") or c["gaia"].get("state") in ("pending", "unavailable"):
                    await self._identify(c)
        left = any(
            c[k].get("state") in ("pending", "unavailable")
            for rec in run.result.get("pairs", [])
            for c in rec["components"]
            for k in ("simbad", "gaia")
        )
        run.result["identified"] = not left
        if not left:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{run.key}.part"
            tmp.write_text(json.dumps(run.result, separators=(",", ":")))
            tmp.replace(self.dir / f"{run.key}.json")

    def view(self, run: PairRun) -> dict[str, Any]:
        """What the movers route adds: pairs, chance_pairs, mjd and the state of the pair search."""
        out: dict[str, Any] = {"pairs_state": run.state, "pairs_error": run.error}
        if run.result:
            out.update({k: run.result[k] for k in ("pairs", "chance_pairs", "mjd", "identified")})
        return out


def round_or_none(x: float | None, n: int = 3) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, n)
