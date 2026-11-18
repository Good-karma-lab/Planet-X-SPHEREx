"""The service's limits follow the machine (service/machine.py): the 16-vCPU / 48 GB VM keeps today's numbers; a 2-OCPU / 12 GB
Always Free A1 turns the background down and keeps a person's field first; the environment overrides each value."""

from __future__ import annotations

import pytest

from service import machine

KNOBS = ("SKY_WORKERS", "SKY_HEAVY_SLOTS", "SKY_MEMORY_BUDGET_MB", "SKY_WORKER_MEM_MB", "SKY_BG_HEAVY", "SKY_QUARTER_STOCK", "SKY_SWEEP")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in KNOBS:
        monkeypatch.delenv(k, raising=False)


def test_the_big_vm_keeps_todays_numbers() -> None:
    lim = machine.limits(cores=16, ram=48000, fence=None)
    assert not lim.small and (lim.workers, lim.heavy_slots, lim.memory_budget_mb, lim.worker_mem_mb) == (16, 6, 24000, 6000)
    assert (lim.bg_heavy, lim.quarter_stock, lim.sweep) == (2, 40, True)


def test_a_small_a1_turns_the_background_down() -> None:
    lim = machine.limits(cores=2, ram=12000, fence=None)
    assert lim.small and (lim.workers, lim.heavy_slots, lim.bg_heavy) == (2, 2, 1)
    assert lim.memory_budget_mb == 6000 and lim.worker_mem_mb == 4200  # half for the jobs; a 60′ field peaks at 1.1 GB
    assert lim.quarter_stock == 8 and lim.sweep is False  # a person's field needs both cores


def test_the_environment_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKY_SWEEP", "1")
    monkeypatch.setenv("SKY_HEAVY_SLOTS", "3")
    lim = machine.limits(cores=2, ram=12000, fence=None)
    assert lim.sweep is True and lim.heavy_slots == 3
    monkeypatch.setenv("SKY_SWEEP", "0")
    assert machine.limits(cores=16, ram=48000, fence=None).sweep is False
    assert machine.ram_mb() > 1000  # this machine's memory is read


def test_the_jobs_budget_stays_inside_the_units_fence() -> None:
    """18 Nov 2026: a 13 GB MemoryMax under a 24 GB budget had the kernel kill the API. The budget is now at most 55 % of the fence,
    and a worker's cap follows the fence too."""
    lim = machine.limits(cores=16, ram=48000, fence=13000)
    assert lim.memory_budget_mb == 7150 and lim.memory_max_mb == 13000 and lim.worker_mem_mb == 4550
    big = machine.limits(cores=16, ram=48000, fence=33600)  # MemoryMax=70 % of the E5
    assert big.memory_budget_mb == 18480 and big.worker_mem_mb == 6000
    a1 = machine.limits(cores=2, ram=12000, fence=8400)  # MemoryMax=70 % of a 12 GB A1
    assert a1.memory_budget_mb == 4620 and a1.worker_mem_mb == 2940
    assert machine.limits(cores=16, ram=48000, fence=None).memory_budget_mb == 24000


def test_a_four_core_24_gb_machine_is_not_small() -> None:
    """The A1 resized to 4 OCPU / 24 GB: the normal profile, one field build per core, the jobs' budget inside its 70 % fence."""
    lim = machine.limits(cores=4, ram=24000, fence=16800)
    assert not lim.small and lim.heavy_slots == 4 and lim.bg_heavy == 2 and lim.memory_budget_mb == 9240
    assert machine.limits(cores=2, ram=12000, fence=None).small and machine.limits(cores=4, ram=12000, fence=None).small
