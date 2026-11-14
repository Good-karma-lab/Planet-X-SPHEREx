"""M2 validation: the exposure index (docs/plan/02-methods/2.7-validation.md, V3) and the footprint margin.

1. Margin: for a sample of files, the 12-vertex footprint through the file's own SIP WCS against IRSA's 4-corner polygon in the index;
   how far any real edge point lies outside the 4-corner polygon. The index adds EDGE_MARGIN_DEG to every overlap test; it must exceed
   the largest bulge found.
2. V3: exposures of the test fields: containing the centre, and overlapping the field (circumscribed circle of the square field).
   The reference counts (Barnard's Star 5′: 384 overlapping, 361 containing the centre; 60′: 603) were made on QR2 + QR3; this index holds
   DR1 + QR3 (DR1 replaces QR2), so the counts are compared, not required to be equal.

Run:  uv run python -m validation.m2_index [--index store/index/exposures.sqlite] [--sample 36]
"""

from __future__ import annotations

import argparse
import asyncio
import random
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from engine.spx.footprint import contains, footprint, radec_to_xyz
from indexer.store import EDGE_MARGIN_DEG, Index, footprint_of
from service.reader.files import FileReader
from service.reader.s3 import S3

ROOT = Path(__file__).resolve().parent.parent
FIELDS = [  # (label, RA, Dec) at the SPHEREx epoch: the places the lab opens by coordinates
    ("Barnard's Star", 269.4479, 4.7687),
    ("ε Indi B", 331.077, -56.794),
    ("ecliptic field", 169.8917, 4.3514),
    ("Eris field", 27.378, -0.120),
]


def outside_distance_arcsec(poly_ra: np.ndarray, poly_dec: np.ndarray, ra: np.ndarray, dec: np.ndarray) -> float:
    """Largest angular distance of the points outside the convex spherical polygon (0 when all are inside)."""
    v = radec_to_xyz(poly_ra, poly_dec)
    n = np.cross(v, np.roll(v, -1, axis=0))
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    # orient normals inward (towards the centroid)
    c = v.mean(axis=0)
    n *= np.sign(n @ c)[:, None]
    p = radec_to_xyz(ra, dec)
    s = p @ n.T  # >0 inside each edge
    out = np.clip(-s, 0, None).max(axis=1)
    return float(np.degrees(np.arcsin(np.clip(out.max(), 0, 1))) * 3600)


async def margin(index: Index, sample: int, store: Path) -> list[tuple[str, int, float]]:
    rows = index.db.execute("SELECT key, det, poly FROM files ORDER BY random() LIMIT ?", (sample,)).fetchall()
    s3 = S3(max_in_flight=12)
    fr = FileReader(s3, store)
    out = []

    async def one(key: str, det: int, poly: bytes) -> None:
        lay = await fr.layout(key)
        fp = footprint(lay.image_header)
        pv = np.frombuffer(poly, np.float64).reshape(2, -1)
        out.append((key, det, outside_distance_arcsec(pv[0], pv[1], fp.ra, fp.dec)))

    await asyncio.gather(*(one(k, d, p) for k, d, p in rows))
    await s3.close()
    return sorted(out, key=lambda t: -t[2])


def counts(index: Index) -> list[str]:
    lines = ["| field | size | containing the centre | overlapping | by release | query ms |", "|---|---|---|---|---|---|"]
    for label, ra, dec in FIELDS:
        for fov in (5.0, 20.0, 60.0):
            t = time.time()
            ov = index.overlapping(ra, dec, fov / 60.0 / 2.0 * 2**0.5)
            ms = 1000 * (time.time() - t)
            inside = 0
            for e in ov:
                (poly,) = index.db.execute("SELECT poly FROM files WHERE id = ?", (e.id,)).fetchone()
                pv = np.frombuffer(poly, np.float64).reshape(2, -1)
                inside += contains(footprint_of(pv[0], pv[1]), ra, dec)
            rel = {r: sum(1 for e in ov if e.release == r) for r in sorted({e.release for e in ov})}
            lines.append(f"| {label} | {fov:.0f}′ | {inside} | {len(ov)} | {rel} | {ms:.0f} |")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, default=ROOT / "store" / "index" / "exposures.sqlite")
    ap.add_argument("--sample", type=int, default=36)
    a = ap.parse_args()
    index = Index(a.index, readonly=True)
    random.seed(1)
    rep = [
        f"# M2 validation — exposure index ({datetime.now(UTC).date().isoformat()})",
        "",
        f"Index: {index.count():,} files; meta: {index.meta().get('bootstrap_at', '?')} from {index.meta().get('source', '?')}.",
        "",
        "## Footprint margin (real SIP edge vs IRSA 4-corner polygon)",
        "",
    ]
    m = asyncio.run(margin(index, a.sample, Path(tempfile.mkdtemp(prefix="sky-m2-"))))
    worst = m[0][2] if m else float("nan")
    by_det: dict[int, float] = {}
    for _, det, d in m:
        by_det[det] = max(by_det.get(det, 0.0), d)
    rep += [
        f"{len(m)} random files; largest distance of a real edge point outside the 4-corner polygon: **{worst:.1f}″** "
        f"(per detector: {', '.join(f'D{k} {v:.1f}″' for k, v in sorted(by_det.items()))}); the index's margin is "
        f"{EDGE_MARGIN_DEG * 3600:.0f}″ — {'enough' if worst < EDGE_MARGIN_DEG * 3600 else '**too small**'}.",
        "",
        "## V3 — exposures of the test fields",
        "",
        *counts(index),
        "",
        "Reference counts (QR2 + QR3 index, 2026-11-12): Barnard's Star 5′ 384 overlapping, 361 containing the centre; 60′ 603.",
    ]
    out = ROOT / "docs" / "validation" / f"{datetime.now(UTC).date().isoformat()}-m2-index.md"
    out.write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep))
    index.close()


if __name__ == "__main__":
    main()
