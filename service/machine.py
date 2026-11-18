"""The service's limits follow the machine it runs on: its cores and its memory.

Measured on the 16-vCPU / 48 GB VM (Nov 2026): six field builds side by side (they wait on the archive more than they compute), a
24 GB memory budget for the jobs, a 6 GB cap per worker process, two background builds at a time, a stock of 40 ready quarters, and the
sweep on. A 2-OCPU / 12 GB Always Free A1 cannot carry that: there the service keeps a person's field first and turns the background
down (fewer slots, a smaller stock, the sweep off unless asked for).

Every value has its environment variable, which wins (SKY_WORKERS, SKY_HEAVY_SLOTS, SKY_MEMORY_BUDGET_MB, SKY_WORKER_MEM_MB, SKY_BG_HEAVY,
SKY_QUARTER_STOCK, SKY_SWEEP). `/health` says what was chosen (`machine`).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

SMALL_CORES = 4  # this many cores or fewer: a small machine
SMALL_RAM_MB = 16000  # this much memory or less: a small machine


def ram_mb() -> int:
    """The machine's memory in MB (Linux and macOS), 8 GB when it cannot be read."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e6)
    except (ValueError, OSError, AttributeError):
        return 8000


@dataclass(frozen=True)
class Limits:
    cores: int
    ram_mb: int
    small: bool
    workers: int  # processes of the compute pool
    heavy_slots: int  # field builds at once
    memory_budget_mb: int  # what the jobs together may hold
    worker_mem_mb: int  # the cap of one worker process
    bg_heavy: int  # background (warm, stock, sweep) builds at once
    quarter_stock: int  # ready quarters kept ahead
    sweep: bool  # the background sweep of the grid

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def limits(cores: int | None = None, ram: int | None = None) -> Limits:
    """The limits for a machine of `cores` cores and `ram` MB (this machine's by default); the environment overrides each one."""
    c = max(1, cores if cores is not None else (os.cpu_count() or 2))
    r = max(1000, ram if ram is not None else ram_mb())
    small = c <= SMALL_CORES or r <= SMALL_RAM_MB
    # memory: half the machine for the jobs (the rest is the API process, the page cache the archive reads live on, the standby)
    budget = min(24000, r // 2)
    sweep_env = os.environ.get("SKY_SWEEP")
    return Limits(
        cores=c,
        ram_mb=r,
        small=small,
        workers=_env_int("SKY_WORKERS", c),
        heavy_slots=_env_int("SKY_HEAVY_SLOTS", 6 if not small else 2),
        memory_budget_mb=_env_int("SKY_MEMORY_BUDGET_MB", budget),
        worker_mem_mb=_env_int("SKY_WORKER_MEM_MB", min(6000, max(1500, r * 35 // 100))),
        bg_heavy=_env_int("SKY_BG_HEAVY", 2 if not small else 1),
        quarter_stock=_env_int("SKY_QUARTER_STOCK", 40 if not small else 8),
        # off on a small machine unless asked for (SKY_SWEEP=1 or force): a person's field needs both cores there
        sweep=(sweep_env not in ("0",)) if sweep_env is not None else not small,
    )
