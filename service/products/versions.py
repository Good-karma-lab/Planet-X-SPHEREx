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
    # the search between passes and its Gaia check; m5 motion grade, m6 names of saturated stars, m7 pair shifts rejected,
    # m8 a saturated centroid track gives way to the saturated-zone track of the star it stands for
    "movers": "m8",
    "anomalies": "a2",  # the spectral anomaly map of a field and the identification of its anomalies; a2: the same passes as the movers
    "red": "r3",  # red sources of a field: 3.8–5 µm against 1.1–1.6 µm. r2: scatter and passes of a moving source; r3: peaks from 4 sigma
    # Find Planet X, one pass: distance search, candidates, photometry, slider arrays.
    # d2: the tuned own motion stays inside the gate, no peaks over a bright fast star
    "distant": "d2",
    "deep": "m1",  # Find Planet X, the sum of passes along one orbit
    "link": "l1",  # Find Planet X, the link of passes by one orbit
    "filmsearch": "t4",  # the unknown movers of a film; t4: every candidate verified, orbit links from 3 sigma
    # moving red sources the catalogues do not explain; known brown dwarfs listed apart, with counts.
    # bd3: a source the movers search found too shows the track's motion and place
    "browndwarfs": "bd3",
    "searched": "s1",  # the list of searched places (/api/v1/searched)
}

DEPENDS: dict[str, tuple[str, ...]] = {
    "field": (),
    "film": (),
    "spectrum": (),
    "movers": ("field",),
    "anomalies": ("field",),
    "red": ("field",),
    "distant": (),
    "deep": (),
    "link": ("distant",),
    "filmsearch": ("film",),
    "browndwarfs": ("red", "movers"),
    "searched": (),
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
