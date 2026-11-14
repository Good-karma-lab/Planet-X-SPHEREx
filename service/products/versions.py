"""Product kinds, their versions and what each is computed from (docs/plan/05-architecture.md 5.6; decision 0003).

A product's key holds its own version and the versions of the products it is computed from, so bumping one version invalidates exactly
the products that depend on it. On start the store purges every version that is not current and the warm runner re-computes the warm
set. Bump a version whenever the product's output changes, and add a change-log line on the Method page in the same commit.
"""

from __future__ import annotations

VERSIONS: dict[str, str] = {
    "field": "f2",  # the stacks of a field (all parts). f2: band hues in the meta
    "film": "v2",  # the frames of the visits of one pass. v2: no motion at coverage edges
    "spectrum": "s1",  # PSF photometry of a place in every exposure that covers it
    "movers": "m3",  # the search between passes of a field and its Gaia check; m2 chance control, m3 corner passes sit out
    "anomalies": "a2",  # the spectral anomaly map of a field and the identification of its anomalies; a2: the same passes as the movers
    "red": "r1",  # red sources of a field: 3.8–5 µm against 1.1–1.6 µm
}

DEPENDS: dict[str, tuple[str, ...]] = {
    "field": (),
    "film": (),
    "spectrum": (),
    "movers": ("field",),
    "anomalies": ("field",),
    "red": ("field",),
}


def lineage(kind: str) -> tuple[str, ...]:
    """The versions that make up a key of this kind: its own and those of everything it depends on, in a fixed order."""
    seen: list[str] = []

    def walk(k: str) -> None:
        for d in DEPENDS[k]:
            walk(d)
        if k not in seen:
            seen.append(k)

    walk(kind)
    return tuple(f"{k}:{VERSIONS[k]}" for k in seen)
