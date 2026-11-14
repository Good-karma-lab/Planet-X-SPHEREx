"""Every field a search ran on (warm set, visitors, background scans) with what the searches found, for the globe layer "Where we've
searched".

One entry per place (fields of one place are merged): which searches ran there (`passes` the search between passes with its pairs and
brown dwarf candidates, `film` the unknown movers of a pass, `distant` and `deep` Find Planet X), how many leads they found, the best
one, and what to open. Only fields whose searches are finished or running are listed (not every field someone merely looked at). The
list is cheap and kept for a minute.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from service import cpu
from service.products.store import Store, product_key
from service.products.versions import VERSIONS

KINDS = ("field", "film", "filmsearch", "distant", "deep")
CACHE_S = 60.0
CELL_DEG = 0.05  # fields whose centres lie in the same cell are one place


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        d: dict[str, Any] = json.loads(path.read_text())
        return d
    except (OSError, ValueError):
        return None


def _centre(kind: str, params: dict[str, Any]) -> tuple[float, float, float] | None:
    if kind == "deep":
        fl = params.get("fields") or []
        return (float(fl[0][0]), float(fl[0][1]), float(params.get("fov", 18))) if fl else None
    if "ra" in params and "dec" in params:
        return float(params["ra"]), float(params["dec"]), float(params.get("fov", 5))
    return None


def summarize(rows: list[dict[str, Any]], dirs: dict[str, str], keys: dict[str, dict[str, str]], running: list[str]) -> dict[str, Any]:
    """The list of searched places from the product rows (a worker process: it reads the small result files).
    `dirs`: where the JSON results of movers, pairs and brown dwarfs are; `keys[field_key]`: the keys of those results for a field;
    `running`: the keys of the products whose job is running."""
    places: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        c = _centre(row["kind"], row["params"])
        if c is None:
            continue
        ra, dec, fov = c
        cell = (round(ra / CELL_DEG), round(dec / CELL_DEG))
        pl = places.setdefault(
            cell,
            {
                "ra": ra,
                "dec": dec,
                "fov": fov,
                "kinds": set(),
                "found": 0,
                "unfinished": False,
                "best": None,
                "t": 0.0,
                "pass": None,
                "film": None,
                "key": row["key"],
            },
        )
        pl["t"] = max(pl["t"], row["created"])
        if row["kind"] == "field":
            pl["ra"], pl["dec"], pl["fov"], pl["key"] = ra, dec, fov, row["key"]
            movers = _read(Path(dirs["movers"]) / f"{keys[row['key']]['movers']}.json")
            pairs = _read(Path(dirs["pairs"]) / f"{keys[row['key']]['pairs']}.json")
            dwarfs = _read(Path(dirs["browndwarfs"]) / f"{keys[row['key']]['browndwarfs']}.json")
            if movers is not None or pairs is not None:
                pl["kinds"].add("passes")
            else:
                pl["unfinished"] = keys[row["key"]]["movers"] in running or keys[row["key"]]["pairs"] in running
            n_tracks = len([t for t in (movers or {}).get("tracks", []) if t.get("check") != "still"])
            n_pairs = len((pairs or {}).get("pairs", []))
            n_dwarfs = len((dwarfs or {}).get("candidates", []))
            pl["found"] += n_tracks + n_pairs + n_dwarfs
            if n_dwarfs:
                _better(pl, {"kind": "track", "label": f"{n_dwarfs} brown dwarf candidate" + ("s" if n_dwarfs > 1 else ""), "sn": None}, 3)
            if n_tracks:
                sns = [s for t in (movers or {})["tracks"] for s in (t.get("snr") or []) if s is not None]
                _better(
                    pl, {"kind": "track", "label": f"{n_tracks} mover" + ("s" if n_tracks > 1 else ""), "sn": max(sns, default=None)}, 2
                )
            if n_pairs:
                _better(pl, {"kind": "pair", "label": f"{n_pairs} changing pair" + ("s" if n_pairs > 1 else ""), "sn": None}, 1)
        elif row["kind"] in ("film", "filmsearch"):
            if row["params"].get("mjd") is not None:
                pl["film"] = row["params"]["mjd"]
            if row["kind"] == "filmsearch":
                meta = _read(Path(dirs["products"]) / "filmsearch" / VERSIONS["filmsearch"] / row["key"][:2] / row["key"] / "meta.json")
                if row["complete"] and meta is not None:
                    pl["kinds"].add("film")
                    n = len(meta.get("tracks", []))
                    pl["found"] += n
                    if n:
                        zs = [t.get("z_together") for t in meta["tracks"] if t.get("z_together") is not None]
                        _better(
                            pl, {"kind": "track", "label": f"{n} moving object" + ("s" if n > 1 else ""), "sn": max(zs, default=None)}, 2
                        )
                else:
                    pl["unfinished"] = pl["unfinished"] or row["key"] in running
        else:  # distant, deep
            meta = _read(Path(dirs["products"]) / row["kind"] / VERSIONS[row["kind"]] / row["key"][:2] / row["key"] / "meta.json")
            if row["complete"] and meta is not None:
                pl["kinds"].add(row["kind"])
                if row["params"].get("pass") is not None:
                    pl["pass"] = row["params"]["pass"]
                peaks = [q for q in meta.get("candidates", []) if q.get("ok")]
                pl["found"] += len(peaks)
                if peaks:
                    top = max(peaks, key=lambda q: _peak_sn(q) or 0.0)
                    _better(pl, {"kind": "peak", "label": f"Peak · {top.get('dr', top.get('d', 0)):.0f} au", "sn": _peak_sn(top)}, 1)
            else:
                pl["unfinished"] = pl["unfinished"] or row["key"] in running
    out = []
    for pl in places.values():
        if not pl["kinds"] and not pl["unfinished"]:
            continue
        out.append(
            {
                "key": pl["key"],
                "ra": round(pl["ra"], 5),
                "dec": round(pl["dec"], 5),
                "fov": pl["fov"],
                "state": "running" if pl["unfinished"] else "done",
                "kinds": sorted(pl["kinds"]),
                "found": pl["found"],
                "nothing": (not pl["unfinished"]) and pl["found"] == 0,
                "best": None if pl["best"] is None else {k: pl["best"][k] for k in ("kind", "label", "sn")},
                "searched_at": _iso(pl["t"]),
                "open": {"ra": round(pl["ra"], 5), "dec": round(pl["dec"], 5), "fov": pl["fov"], "pass": pl["pass"], "film": pl["film"]},
            }
        )
    out.sort(key=lambda f: f["searched_at"], reverse=True)
    return {"count": len(out), "fields": out}


def _peak_sn(q: dict[str, Any]) -> float | None:
    v = q.get("phot_sn") or q.get("sn_tuned") or q.get("sn")
    return None if v is None else float(v)


def _better(pl: dict[str, Any], cand: dict[str, Any], rank: int) -> None:
    cand["rank"] = rank
    if pl["best"] is None or (rank, cand["sn"] or 0.0) > (pl["best"]["rank"], pl["best"]["sn"] or 0.0):
        pl["best"] = cand


class Searched:
    """The list of searched places, built from the product store and the searches' result files, kept for a minute."""

    def __init__(self, store: Store, store_dir: Path, scheduler: Any, movers: Any, pairs: Any, dwarfs: Any) -> None:
        self.store, self.store_dir, self.scheduler = store, store_dir, scheduler
        self.movers, self.pairs, self.dwarfs = movers, pairs, dwarfs
        self._at = 0.0
        self._body: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, Any]:
        async with self._lock:
            if self._body is not None and time.monotonic() - self._at < CACHE_S:
                return self._body
            rows = self.store.listing(KINDS)
            keys = {
                r["key"]: {
                    "movers": self.movers.key_of(r["key"]),
                    "pairs": self.pairs.key_of(r["key"]),
                    "browndwarfs": self.dwarfs.key_of(r["key"]),
                }
                for r in rows
                if r["kind"] == "field"
            }
            running = [j.key for j in self.scheduler.jobs.values() if j.state in ("queued", "running", "paused")]
            for runs in (self.movers.runs, self.pairs.runs):  # the searches that run without the scheduler
                running += [r.key for r in runs.values() if r.state == "running"]
            dirs = {
                "movers": str(self.movers.dir),
                "pairs": str(self.pairs.dir),
                "browndwarfs": str(self.dwarfs.dir),
                "products": str(self.store.root / "products"),
            }
            body: dict[str, Any] = await cpu.run(summarize, rows, dirs, keys, running)
            self._body, self._at = body, time.monotonic()
            return body


def searched_key(kind: str, params: dict[str, Any]) -> str:
    return product_key(kind, params)
