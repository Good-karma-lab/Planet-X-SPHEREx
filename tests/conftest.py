from __future__ import annotations

import os

import pytest

os.environ.setdefault("SKY_WARM", "0")  # the warm runner is tested on its own; services built in tests do not warm
os.environ.setdefault("SKY_KNOWN_PREWARM", "0")  # a finished field does not ask the catalogues from a test


@pytest.fixture(autouse=True)
def _every_kind_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tasks' tests walk every kind of find; the kinds switched off for now have their own test (which switches them off)."""
    from service.accounts import people

    monkeypatch.setattr(people, "KINDS_OFF", frozenset())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("SKY_NET") == "1":
        return
    skip = pytest.mark.skip(reason="needs the network (set SKY_NET=1)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _jpl_without_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests talk to fake JPLs: no spacing between their requests (a test of the spacing sets its own)."""
    from service.upstream import jpl

    monkeypatch.setattr(jpl, "GAP_S", 0.0)
    monkeypatch.setattr(jpl, "SBIDENT_GAP_S", 0.0)
    jpl._next_at.clear()
