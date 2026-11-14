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


def load() -> Config:
    return Config()
