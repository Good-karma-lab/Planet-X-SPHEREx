"""The place of a moving object at an exposure's time, and the parameters that key a spectrum."""

from __future__ import annotations

from pathlib import Path

import pytest

from service.spectrum import place_at, spectrum_params


def test_fixed_place_does_not_move() -> None:
    p = spectrum_params(269.3559, 4.7822)
    assert p == {"ra": 269.3559, "dec": 4.7822}
    assert place_at(p, 61000.0) == (269.3559, 4.7822)


def test_proper_motion_moves_the_place() -> None:
    # Barnard's Star: −801.551, +10362.394 mas/yr from J2000; 25 years later about 4.3′ north
    p = spectrum_params(269.452077, 4.693365, -801.551, 10362.394)
    ra, dec = place_at(p, 51544.5 + 25 * 365.25)
    assert dec - 4.693365 == pytest.approx(10.362394 * 25 / 3600, rel=1e-6)
    assert (ra - 269.452077) * 3600 * 1000 / 25 == pytest.approx(-801.551 / 0.99666, rel=2e-3)


def test_a_run_kept_without_exposure_keys_is_measured_again(tmp_path: Path) -> None:
    """A spectrum measured before its points carried their exposure's key cannot have people's flags applied: it counts as not kept."""
    import json

    from service.spectrum import Spectra, spectrum_params

    sp = Spectra(None, None, None, tmp_path)  # type: ignore[arg-type]
    params = spectrum_params(269.45, 4.69)
    sp.dir.mkdir(parents=True, exist_ok=True)
    path = sp.dir / f"{sp.key_of(params)}.json"
    path.write_text(json.dumps({"params": params, "total": 1, "points": [{"i": 0, "ok": True}]}))
    assert sp.get(params) is None
    path.write_text(json.dumps({"params": params, "total": 1, "points": [{"i": 0, "key": "a", "ok": True}]}))
    got = sp.get(params)
    assert got is not None and got.points[0]["key"] == "a"


def test_a_finished_run_keeps_no_stamps_and_reads_them_when_asked(tmp_path: Path) -> None:
    """The stamps of a place with many visits are a million objects: they stay on disk, whether the spectrum was just measured or read."""
    import asyncio
    from types import SimpleNamespace

    from service.spectrum import Spectra

    exps = [SimpleNamespace(key=f"k{i}", mjd0=61000.0 + i, det=1, release="qr2") for i in range(3)]
    sp = Spectra(SimpleNamespace(overlapping=lambda *a: exps), None, None, tmp_path)  # type: ignore[arg-type]
    held: list[int] = []

    async def measure(params: dict[str, float], e: SimpleNamespace) -> dict[str, object]:
        held.append(len(sp.runs[sp.key_of(params)].stamps_view()["stamps"]))
        return {"ok": False, "why": "x", "_stamp": {"status": 1, "raw": [[float(e.mjd0), None]]}}

    sp.measure = measure  # type: ignore[assignment,method-assign]
    sp.sem = asyncio.Semaphore(1)
    params = spectrum_params(10.0, 20.0)

    async def go() -> None:
        run = sp.start(params)
        assert run.task is not None
        await run.task

    asyncio.run(go())
    run = sp.get(params)
    assert run is not None and run.state == "done" and not run.stamps
    assert held == [0, 1, 2]  # while it is measured the page gets what there is
    want = [{"i": i, "status": 1, "raw": [[61000.0 + i, None]]} for i in range(3)]
    assert run.stamps_view()["stamps"] == want and run.stamps_view(2)["stamps"] == want[2:]
    assert not run.stamps
    again = Spectra(None, None, None, tmp_path).get(params)  # type: ignore[arg-type]
    assert again is not None and not again.stamps and again.stamps_view()["stamps"] == want
