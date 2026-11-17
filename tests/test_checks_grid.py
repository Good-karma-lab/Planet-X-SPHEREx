"""above_bar reads the bar the sweep stored for the square (service.grid.bar_at through Checks._square), as on a real searched square."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from service import grid
from service.checks import Checks, check_above_bar
from service.grid import GridDB


def test_above_bar_names_the_squares_bar(tmp_path: Path) -> None:
    db = GridDB(tmp_path / "product.sqlite")
    ce = grid.by_id("294-0148")
    bar = {"kind": "sweep", "mover_sigma": 5.0, "mover_shift_arcsec": 6.15, "passes": 3, "passes_used": 2}
    db.put(ce.id, "searched", finds=0, find_ids=[], bar=bar, field_key="k", source="sweep")
    sq = Checks._square(SimpleNamespace(st=SimpleNamespace(grid=db)), ce.ra, ce.dec)  # type: ignore[arg-type]
    assert sq is not None and sq["id"] == ce.id and sq["bar"]["mover_sigma"] == 5.0
    marks = [{"pass": 0, "ra": ce.ra, "dec": ce.dec, "mjd": 61000.0}, {"pass": 1, "ra": ce.ra + 3e-4, "dec": ce.dec, "mjd": 61180.0}]
    state, detail = check_above_bar(marks, {"tracks": []}, sq)  # type: ignore[arg-type]
    assert state == "warn" and "square 294-0148: a motion of 5σ and a shift of 6.15″ over 3 passes" in detail
    assert Checks._square(SimpleNamespace(st=SimpleNamespace(grid=db)), 120.0, -30.0) is None  # type: ignore[arg-type]
