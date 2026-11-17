"""The exposures people agreed are broken, left out of stacks and spectra (service/exposure_flags.py): a flag changes the key of the
fields it touches and of no other; a spectrum's broken points are marked left out when it is served."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from service import exposure_flags
from service.workers import fieldjob


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    exposure_flags.set_bad(set())
    yield
    exposure_flags.set_bad(set())


class Index:
    def __init__(self, by_place: dict[float, list[str]]) -> None:
        self.by_place = by_place

    def overlapping(self, ra: float, dec: float, radius: float) -> list[Any]:
        return [SimpleNamespace(key=k) for k in self.by_place[ra]]


def test_a_flag_changes_the_key_of_the_fields_it_touches_only() -> None:
    idx = Index({10.0: ["a", "b", "c"], 20.0: ["d", "e"]})
    here, there = {"ra": 10.0, "dec": 0.0, "fov": 10.0}, {"ra": 20.0, "dec": 0.0, "fov": 10.0}
    before_here, before_there = fieldjob.exposure_signature(idx, here), fieldjob.exposure_signature(idx, there)  # type: ignore[arg-type]
    v = exposure_flags.version()
    assert exposure_flags.set_bad({"b"}) and exposure_flags.version() == v + 1 and not exposure_flags.set_bad({"b"})
    assert fieldjob.exposure_signature(idx, here) != before_here  # type: ignore[arg-type]
    assert fieldjob.exposure_signature(idx, there) == before_there  # type: ignore[arg-type]  # a field the flag does not touch
    exposure_flags.set_bad(set())
    assert fieldjob.exposure_signature(idx, here) == before_here  # type: ignore[arg-type]  # the same key as before any flag


def test_a_broken_exposure_is_left_out_of_the_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_field reads its exposures through the index: the broken one never reaches the stack."""
    from service.workers import fieldbuild

    seen: list[list[str]] = []

    def thin(found: list[Any]) -> list[Any]:
        seen.append([e.key for e in found])
        raise RuntimeError("stop here")  # the selection is what this test looks at

    monkeypatch.setattr(fieldbuild, "thin", thin)
    exposure_flags.set_bad({"b"})
    import asyncio

    with pytest.raises(RuntimeError, match="stop here"):
        asyncio.run(fieldbuild.build_field(Index({10.0: ["a", "b", "c"]}), SimpleNamespace(), 10.0, 0.0, 10.0))  # type: ignore[arg-type]
    assert seen == [["a", "c"]]


def test_spectrum_points_of_broken_and_touched_exposures_are_marked() -> None:
    pts = [{"key": "a", "ok": True}, {"key": "b", "ok": True}, {"key": "c", "ok": True}, {"key": "d", "ok": False, "why": "saturated"}]
    exposure_flags.set_bad({"b", "d"})
    out = exposure_flags.mark_points(pts, neighbour={"c"})
    assert [p["ok"] for p in out] == [True, False, False, False]
    assert out[1]["why"] == exposure_flags.BROKEN_WHY and out[2]["why"] == exposure_flags.NEIGHBOUR_WHY and out[3]["why"] == "saturated"
    assert pts[1]["ok"] is True  # the run's own points are not changed
