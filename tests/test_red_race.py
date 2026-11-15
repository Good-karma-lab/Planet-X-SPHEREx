"""Two asks for the red sources of one field at once (the warm runner and a visitor) compute once and both get the answer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from service import red


def test_two_asks_at_once_compute_once_and_never_race_on_the_file(tmp_path: Path, monkeypatch: Any) -> None:
    calls = {"n": 0}

    def read_parts(store: Any, field_key: str) -> tuple[dict[str, Any], list[Any]]:
        return {"passes": []}, []

    async def run(fn: Any, *a: Any) -> dict[str, Any]:
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return {"field": a[0], "sources": 0, "red": [], "all": [], "median_colour": None, "bands": ""}

    monkeypatch.setattr(red, "read_parts", read_parts)
    monkeypatch.setattr(red.cpu, "run", run)

    async def go() -> list[dict[str, Any]]:
        return await asyncio.gather(*(red.find_red_async(object(), "f" * 24, tmp_path) for _ in range(4)))  # type: ignore[arg-type]

    out = asyncio.run(go())
    assert calls["n"] == 1 and all(o["sources"] == 0 for o in out)
    assert len(list(tmp_path.glob("*.json"))) == 1 and not list(tmp_path.glob("*.part"))
    assert asyncio.run(red.find_red_async(object(), "f" * 24, tmp_path))["sources"] == 0 and calls["n"] == 1  # now from the kept file
