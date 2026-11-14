from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("SKY_NET") == "1":
        return
    skip = pytest.mark.skip(reason="needs the network (set SKY_NET=1)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
