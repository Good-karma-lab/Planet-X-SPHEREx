"""Named bands: sets of wavelength windows (docs/plan/02-methods/2.3-views.md, 2.3.3).

One source of truth, ``data/bands.json``, is read by the engine, the web app and the Method page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BANDS_FILE = DATA_DIR / "bands.json"

WAVELENGTH_MIN_UM = 0.75
WAVELENGTH_MAX_UM = 5.0


@dataclass(frozen=True)
class Band:
    id: int
    key: str
    name: str
    label: str
    hue: int
    windows: tuple[tuple[float, float], ...]

    def contains(self, wavelength_um: float) -> bool:
        """Half-open windows: the lower edge is included, the upper edge is not."""
        return any(lo <= wavelength_um < hi for lo, hi in self.windows)


def load_bands(path: Path = BANDS_FILE) -> list[Band]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    bands = [
        Band(
            id=int(b["id"]),
            key=str(b["key"]),
            name=str(b["name"]),
            label=str(b["label"]),
            hue=int(b["hue"]),
            windows=tuple((float(lo), float(hi)) for lo, hi in b["windows"]),
        )
        for b in raw["bands"]
    ]
    validate(bands)
    return bands


def validate(bands: list[Band]) -> None:
    ids = [b.id for b in bands]
    keys = [b.key for b in bands]
    if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
        raise ValueError("band ids and keys must be unique")
    for b in bands:
        if not b.windows:
            raise ValueError(f"band {b.key} has no window")
        for lo, hi in b.windows:
            if not (WAVELENGTH_MIN_UM <= lo < hi <= WAVELENGTH_MAX_UM):
                raise ValueError(f"band {b.key}: window {lo}-{hi} outside {WAVELENGTH_MIN_UM}-{WAVELENGTH_MAX_UM} um")
