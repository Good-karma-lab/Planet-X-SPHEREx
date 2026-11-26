"""Service configuration from the environment (no secrets in the repository)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@dataclass(frozen=True)
class Config:
    store_dir: Path = field(default_factory=lambda: Path(os.environ.get("SKY_STORE", "store")).resolve())
    web_dist: Path = field(default_factory=lambda: Path(os.environ.get("SKY_WEB_DIST", DEFAULT_WEB_DIST)))
    serve_web: bool = field(default_factory=lambda: os.environ.get("SKY_SERVE_WEB", "1") == "1")


def hide_faint_peaks() -> bool:
    """Whether the faint Planet X peaks stand behind their own filter (service/findings.faint_peak): SKY_HIDE_FAINT_PEAKS=0 turns it off.
    Read at every judging: nothing of it is kept in the database."""
    return os.environ.get("SKY_HIDE_FAINT_PEAKS", "1") == "1"


def hide_unseen_peaks() -> bool:
    """Whether the Planet X peaks a person cannot see stand behind the faint filter too (service/findings.unseen_why: no sum along the
    orbit kept, or its dot under service/clips.SIGHT_Z in it): SKY_HIDE_UNSEEN_PEAKS=0 turns it off. Read at every judging."""
    return os.environ.get("SKY_HIDE_UNSEEN_PEAKS", "1") == "1"


def gc_freeze() -> bool:
    """Whether the objects alive once the service is warm are frozen out of the collector's walks (service/memwatch.settle):
    SKY_GC_FREEZE=0 and a restart turns it off."""
    return os.environ.get("SKY_GC_FREEZE", "1") == "1"


def load() -> Config:
    return Config()
