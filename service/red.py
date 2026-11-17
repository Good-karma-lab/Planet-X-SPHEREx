"""Red sources of a finished field (engine/red.py) as a product: computed once from the published detector stacks, kept on disk."""

from __future__ import annotations

import asyncio
import json
import math
import secrets
from pathlib import Path
from typing import Any

import numpy as np

from engine.grid import Grid
from engine.red import red_sources
from service import cpu, jsonsafe
from service.products.store import Store, product_key
from service.products.sxp import decode
from service.workers import fieldjob

KIND = "red"


def find_red(store: Store, field_key: str, out_dir: Path) -> dict[str, Any]:
    key = product_key(KIND, {"field": field_key})
    path = out_dir / f"{key}.json"
    if path.is_file():
        cached: dict[str, Any] = json.loads(path.read_text())
        return cached
    meta, raws = read_parts(store, field_key)
    return keep(red_result(field_key, meta, raws), out_dir, key)


async def find_red_async(store: Store, field_key: str, out_dir: Path) -> dict[str, Any]:
    """The same, with the reading in a thread and the compute in a worker process (never in the API process)."""
    key = product_key(KIND, {"field": field_key})
    path = out_dir / f"{key}.json"
    if path.is_file():
        cached: dict[str, Any] = json.loads(await asyncio.to_thread(path.read_text))
        return cached
    # one computation per field at a time: the warm runner and a visitor asking together share it (two writers of the same file raced)
    task = _running.get(key)
    if task is None:
        task = asyncio.ensure_future(_compute(store, field_key, out_dir, key))
        _running[key] = task
        task.add_done_callback(lambda _t: _running.pop(key, None))
    return await asyncio.shield(task)


_running: dict[str, asyncio.Future[dict[str, Any]]] = {}


async def _compute(store: Store, field_key: str, out_dir: Path, key: str) -> dict[str, Any]:
    meta, raws = await asyncio.to_thread(read_parts, store, field_key)
    result = await cpu.run(red_result, field_key, meta, raws)
    return await asyncio.to_thread(keep, result, out_dir, key)


def read_parts(store: Store, field_key: str) -> tuple[dict[str, Any], list[tuple[bytes, bytes]]]:
    meta = store.read_meta(fieldjob.KIND, field_key)
    if meta is None:
        raise ValueError("the field is not finished")
    raws = []
    for p in meta["passes"]:
        raw = store.read_part(fieldjob.KIND, field_key, f"dets_p{p['index']}.sxp")
        q = store.read_part(fieldjob.KIND, field_key, f"quick_p{p['index']}.sxp")
        if raw is None or q is None:
            raise ValueError("a pass of the field is missing")
        raws.append((raw, q))
    return meta, raws


def red_result(field_key: str, meta: dict[str, Any], raws: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    """Red sources from the passes' detector stacks (runs in a worker process)."""
    dets, noise, sats = [], [], []
    for raw, q in raws:
        _, a = decode(raw)
        _, b = decode(q)
        dets.append(a["dets"].astype(np.float32))
        noise.append(a["noise"].astype(np.float32))
        sats.append(b["sat"].astype(np.float32) / 255.0)
    g = meta["grid"]
    grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
    found = red_sources(dets, noise, g["step_arcsec"], sats)
    rows = []
    for r in found:
        ra, dec = grid.grid_to_sky([r.x], [r.y])
        row: dict[str, Any] = {
            "ra": round(float(ra[0]), 6),
            "dec": round(float(dec[0]), 6),
            "colour": round(r.colour, 2),
            "lower_limit": r.lower_limit,
            "snr_long": round(r.snr_long, 1),
            "snr_short": round(r.snr_short, 1),
            "red": bool(r.red),
            "moves": bool(r.moves),
        }
        if r.moves and r.path and len(r.path) >= 2:
            # the motion from the long-wavelength peaks of the passes (east and north, mas/yr) and the J2000 place on that line
            t = np.array([(meta["passes"][k]["mjd_mid"] - 51544.5) / 365.25 for k, _, _ in r.path])
            pra, pdec = grid.grid_to_sky([x for _, x, _ in r.path], [y for _, _, y in r.path])
            cosd = math.cos(math.radians(float(np.mean(pdec))))
            ye, yn = (np.asarray(pra) - pra[0]) * cosd * 3.6e6, (np.asarray(pdec) - pdec[0]) * 3.6e6
            fe, fn = np.polyfit(t, ye, 1), np.polyfit(t, yn, 1)
            ve, vn = float(fe[0]), float(fn[0])
            # the scatter of the peaks of the passes about that line (arcsec): what the motion is worth
            rms = float(np.sqrt(np.mean(((ye - np.polyval(fe, t)) ** 2 + (yn - np.polyval(fn, t)) ** 2) / 1e6)))
            ra0 = float(pra[0]) - ve * float(t[0]) / 3.6e6 / cosd
            dec0 = float(pdec[0]) - vn * float(t[0]) / 3.6e6
            row.update(
                pmra=round(ve, 1),
                pmdec=round(vn, 1),
                ra_j2000=round(ra0 % 360.0, 6),
                dec_j2000=round(dec0, 6),
                path_rms_arcsec=round(rms, 2),
                path_passes=[int(meta["passes"][k]["index"]) for k, _, _ in r.path],
            )
        rows.append(row)
    seen = [x["colour"] for x in rows if not x["lower_limit"] and x["snr_short"] >= 5]
    result = {
        "field": field_key,
        "sources": len(rows),
        "median_colour": round(float(np.median(seen)), 3) if seen else None,
        "bands": "colour = flux at 3.8–5 µm (D5–D6) / flux at 1.1–1.6 µm (D2)",
        "red": [x for x in rows if x["red"]],
        "all": rows[:60],
    }
    return result


def keep(result: dict[str, Any], out_dir: Path, key: str) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"{key}.{secrets.token_hex(4)}.part"  # its own file: two writers never share one
    tmp.write_text(jsonsafe.dumps(result, separators=(",", ":")))
    tmp.replace(out_dir / f"{key}.json")
    return result
