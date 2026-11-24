from __future__ import annotations

import os

import pytest

os.environ.setdefault("SKY_WARM", "0")  # the warm runner is tested on its own; services built in tests do not warm
os.environ.setdefault("SKY_KNOWN_PREWARM", "0")  # a finished field does not ask the catalogues from a test


@pytest.fixture(autouse=True)
def _every_kind_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tasks' tests walk every kind of find with bare rows; the rule of the tracks' visits has its own test."""
    from service.accounts import people

    monkeypatch.setattr(people, "KINDS_OFF", frozenset())
    monkeypatch.setattr("service.routes_tasks.PROMISING_SPREAD", 1)  # the order itself
    monkeypatch.setattr("service.routes_tasks.PLAIN_SHARE", 0.0)
    monkeypatch.setattr(people, "track_seen", lambda row: row.get("visible") is not False or "raised" in row)


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
