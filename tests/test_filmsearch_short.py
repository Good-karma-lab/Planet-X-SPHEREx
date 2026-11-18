"""A 3-visit orbit track is drawn only when it is stronger than every 3-visit track chance built in the trials (engine/filmsearch)."""

from __future__ import annotations

import numpy as np

from engine.filmsearch import SHORT_NVIS, orbit_assemble


def test_the_short_bar_comes_from_the_trials() -> None:
    inp = {"ok": [0, 1, 2], "ra0": 10.0, "dec0": 0.0}
    # 12 trials: chance makes 3-visit tracks (1.83 a trial at 3, none at 4) whose strongest is 14.2 sigma together
    counts = [[5, 5, 5, 2, 0, 0, 0, 0, 0, 0, 0, 9.0 + k * 0.4] for k in range(12)]
    tracks, ctl = orbit_assemble(inp, [], counts, [], {}, {}, np.zeros(1), np.zeros((1, 8, 8), np.float32), 6.0)
    assert tracks == [] and ctl["min_visits_drawn"] == 4
    assert ctl["short"] == {"visits": SHORT_NVIS, "bar_z_together": 13.4, "trials": 12}
    # the old control format (11 counts, no strengths) still reads, and draws no short track: strengths were not measured
    _, old = orbit_assemble(
        inp, [], [[5, 5, 5, 0, 0, 0, 0, 0, 0, 0, 0]] * 12, [], {}, {}, np.zeros(1), np.zeros((1, 8, 8), np.float32), 6.0
    )
    assert old["min_visits_drawn"] == 4 and "short" not in old
