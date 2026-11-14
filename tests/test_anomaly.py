"""The spectral anomaly map on synthetic spectra: stars of one family and one source with an excess."""

from __future__ import annotations

import numpy as np

from engine.anomaly import bin_spectrum, score_field


def star(rng: np.random.Generator, temp: float, scale: float, excess: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    lam = rng.uniform(0.75, 5.0, 400)
    x = 1.44e4 / (lam * temp)
    f = scale / lam**5 / (np.exp(x) - 1.0)
    f = f / f.mean()
    f = f * (1.0 + excess * np.exp(-0.5 * ((lam - 4.3) / 0.15) ** 2))
    return lam, f * (1.0 + rng.normal(0, 0.02, lam.size))


def test_the_odd_source_is_the_anomaly() -> None:
    rng = np.random.default_rng(3)
    spectra = [star(rng, rng.uniform(4500, 6000), rng.uniform(1, 10)) for _ in range(40)]
    spectra.append(star(rng, 5000, 5, excess=3.0))
    binned = [bin_spectrum(lam, f) for lam, f in spectra]
    fs = score_field(binned, [float(np.nansum(b.med)) for b in binned])
    assert fs.reason == ""
    flagged = [i for i, s in enumerate(fs.scores) if s is not None and s.anomalous]
    assert flagged == [40]
    top = fs.scores[40]
    assert top is not None and top.drivers and 3.8 < top.drivers[0]["lo"] < 4.5 and top.drivers[0]["z"] > 0


def test_too_few_sources_give_no_map() -> None:
    rng = np.random.default_rng(1)
    binned = [bin_spectrum(*star(rng, 5000, 1)) for _ in range(5)]
    fs = score_field(binned, [1.0] * 5)
    assert "cannot be defined" in fs.reason and all(s is None for s in fs.scores)
