"""`GET /api/v1/export/field`: everything the searches found in a field, as one JSON or CSV file to take away.

JSON: {field, generated, states, tracks, pairs, brown_dwarfs, red_sources, anomalies} (the pairs without their picture crops).
CSV: one row per find with the columns of COLUMNS (kind: mover, pair, brown_dwarf_candidate, known_brown_dwarf, red_source, anomaly),
the place at 2025.9 in degrees, the speed in arcseconds per year, and what the catalogues say. A search that has not finished is named in
`states` (JSON) or in the X-Export-Pending header (CSV): the file holds what exists now.
"""

from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from service.red import find_red_async
from service.workers import fieldjob

COLUMNS = ["kind", "id", "ra", "dec", "epoch", "speed_arcsec_per_year", "pa_deg", "snr", "colour", "status", "name", "gaia", "note"]


def _row(**kw: Any) -> dict[str, Any]:
    return {c: kw.get(c, "") for c in COLUMNS}


def csv_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The finds of an export body, one flat row each."""
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(body["tracks"]):
        snr = [s for s in (t.get("snr") or []) if s is not None]
        g = t.get("gaia") or {}
        rows.append(
            _row(
                kind="mover",
                id=i,
                ra=t.get("ra_2025_9"),
                dec=t.get("dec_2025_9"),
                epoch=2025.9,
                speed_arcsec_per_year=t.get("speed"),
                pa_deg=t.get("pa"),
                snr=max(snr) if snr else t.get("snr_long", ""),
                colour=t.get("colour", ""),
                status=t.get("check", ""),
                name=t.get("name") or "",
                gaia=g.get("source_id", ""),
                note="found in the red bands" if t.get("source") == "red" else f"passes {t.get('passes')}",
            )
        )
    for i, p in enumerate(body["pairs"]):
        c1 = (p.get("components") or [{}])[0]
        c2 = (p.get("components") or [{}, {}])[1] if len(p.get("components") or []) > 1 else {}
        rows.append(
            _row(
                kind="pair",
                id=i,
                ra=c1.get("ra"),
                dec=c1.get("dec"),
                epoch=2025.9,
                snr=c1.get("snr", ""),
                status=p.get("verdict", ""),
                name=(c1.get("simbad") or {}).get("name", ""),
                gaia=(c1.get("gaia") or {}).get("source_id", ""),
                note=(
                    f"component 2 at {c2.get('ra')} {c2.get('dec')}; separation {p.get('separation_arcsec')} arcsec; "
                    f"change {p.get('change_sigma')} sigma"
                ),
            )
        )
    for group, kind in (("candidates", "brown_dwarf_candidate"), ("known", "known_brown_dwarf")):
        for i, c in enumerate(body["brown_dwarfs"].get(group, [])):
            rows.append(
                _row(
                    kind=kind if c.get("status") == "known brown dwarf" or group == "candidates" else "known_object",
                    id=i,
                    ra=c.get("ra"),
                    dec=c.get("dec"),
                    epoch=2025.9,
                    speed_arcsec_per_year=round(float(c.get("pm", 0.0)) / 1000.0, 3),
                    pa_deg=c.get("pa"),
                    snr=c.get("snr_long"),
                    colour=c.get("colour"),
                    status=c.get("status", ""),
                    name=(c.get("simbad") or {}).get("name", ""),
                    gaia=(c.get("gaia") or {}).get("verdict", ""),
                )
            )
    for i, r in enumerate(body["red_sources"]):
        rows.append(
            _row(
                kind="red_source",
                id=i,
                ra=r.get("ra"),
                dec=r.get("dec"),
                epoch="",
                snr=r.get("snr_long"),
                colour=r.get("colour"),
                status="moving" if r.get("moves") else "",
                note="lower limit (not seen at 1.1-1.6 um)" if r.get("lower_limit") else "",
            )
        )
    for i, a in enumerate(body["anomalies"]):
        rows.append(
            _row(
                kind="anomaly",
                id=i,
                ra=a.get("ra"),
                dec=a.get("dec"),
                epoch=2025.9,
                snr=a.get("snr"),
                status="moves" if a.get("moves") else "",
                name=(a.get("simbad") or {}).get("name", ""),
                gaia=(a.get("gaia") or {}).get("source_id", ""),
                note=f"spectral anomaly score {a.get('score')}",
            )
        )
    return rows


def to_csv(body: dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(csv_rows(body))
    return out.getvalue()


def make_export_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    from service.products.store import product_key

    router = APIRouter()

    @router.get(f"{prefix}/export/field")
    async def export_field(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        fov: float = Query(5, gt=0, le=60),
        format: str = Query("json", pattern="^(json|csv)$"),
    ) -> Any:
        if st.index is None or st.store is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra, dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if not st.store.is_complete(key):
            return JSONResponse({"state": "waiting", "error": "the field is not finished: open it first"}, status_code=409)
        states: dict[str, str] = {}
        tracks: list[dict[str, Any]] = []
        pairs: list[dict[str, Any]] = []
        dwarfs: dict[str, Any] = {"candidates": [], "known": [], "counts": None}
        red_sources: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []
        if st.movers is not None:
            run = st.movers.get_or_start(key)
            states["movers"] = run.state
            tracks = [t for t in (run.result or {}).get("tracks", []) if t.get("check") != "still"]
        if getattr(st, "pairs", None) is not None:
            prun = st.pairs.get_or_start(key)
            states["pairs"] = prun.state
            pairs = [{k: v for k, v in p.items() if k != "crops"} for p in (prun.result or {}).get("pairs", [])]
        if getattr(st, "dwarfs", None) is not None:
            drun = st.dwarfs.get_or_start(key)
            states["browndwarfs"] = drun.state
            r = drun.result or {}
            dwarfs = {"candidates": r.get("candidates", []), "known": r.get("known", []), "counts": r.get("counts")}
            tracks += st.dwarfs.red_tracks(drun) if drun.state == "done" else []
            dwarfs["candidates"] = [{k: v for k, v in c.items() if k not in ("gaia_check", "track")} for c in dwarfs["candidates"]]
            dwarfs["known"] = [{k: v for k, v in c.items() if k not in ("gaia_check", "track")} for c in dwarfs["known"]]
        try:
            red = await find_red_async(st.store, key, st.dwarfs.store_dir / "red") if getattr(st, "dwarfs", None) is not None else {}
            red_sources = list(red.get("all", []))
            states["red"] = "done"
        except ValueError:
            states["red"] = "waiting"
        if st.anomalies is not None:
            arun = st.anomalies.get(key)
            states["anomalies"] = arun.state if arun is not None else "not started"
            anomalies = list(((arun.result if arun is not None else None) or {}).get("anomalies", []))
        body = {
            "field": {"key": key, "ra": params["ra"], "dec": params["dec"], "fov": params["fov"]},
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "states": states,
            "tracks": tracks,
            "pairs": pairs,
            "brown_dwarfs": dwarfs,
            "red_sources": red_sources,
            "anomalies": anomalies,
        }
        name = f"spherex_field_{params['ra']:.3f}_{params['dec']:.3f}_{params['fov']:g}"
        pending = ",".join(k for k, v in states.items() if v in ("running", "waiting", "not started", "pending"))
        headers = {"Content-Disposition": f'attachment; filename="{name}.{format}"', "X-Export-Pending": pending}
        if format == "csv":
            return Response(to_csv(body), media_type="text/csv; charset=utf-8", headers=headers)
        return JSONResponse(body, headers=headers)

    return router
