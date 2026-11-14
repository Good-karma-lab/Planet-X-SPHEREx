"""The FLAGS bit table of a Level-2 file and the masks built from it (docs/plan/02-methods/2.1-data-and-reading.md, 2.1.3).

Bit numbers are read from each file's own table (``MP_<NAME>`` cards in QR2, ``MSKNnnnn``/``MSKMnnnn`` in QR3 and DR1). The built-in
table is used only when a file has none. Masks are defined by flag NAMES, so a renumbered bit cannot change their meaning.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from astropy.io import fits

# The union of the tables seen in QR2 (v20) and QR3/DR1 (v27) files; the two agree on every name they share.
BUILTIN: dict[str, int] = {
    "TRANSIENT": 0,
    "OVERFLOW": 1,
    "SUR_ERROR": 2,
    "PHANTOM": 4,
    "REFERENCE": 5,
    "NONFUNC": 6,
    "DICHROIC": 7,
    "MISSING_DATA": 9,
    "HOT": 10,
    "COLD": 11,
    "FULLSAMPLE": 12,
    "PHANMISS": 14,
    "NONLINEAR": 15,
    "PERSIST": 17,
    "OUTLIER": 19,
    "CROSSTALK": 20,
    "SOURCE": 21,
    "GHOST": 22,
    "GHOST_FPA": 23,
    "GHOST_EXT": 24,
    "STREAK": 25,
    "BLOOM": 26,
    "SNOWBALL": 27,
    "HALO": 28,
}

# Masked for every stack of the fixed sky (bits 4, 5, 6, 9, 10, 11, 19, 27 in the tables above).
BAD_ALWAYS = ("PHANTOM", "REFERENCE", "NONFUNC", "MISSING_DATA", "HOT", "COLD", "OUTLIER", "SNOWBALL")
# Masked only farther than 6 px from a bright target (bits 0, 1, 2, 17).
BAD_FAR = ("TRANSIENT", "OVERFLOW", "SUR_ERROR", "PERSIST")
SATURATION = ("OVERFLOW",)
# Kept for every search for moving objects and the photometry of a moving body: the pipeline sets it on asteroid pixels.
MOVER_KEEP = ("OUTLIER",)

# Display groups of the artifact mask and hover.
GROUPS: dict[str, tuple[str, ...]] = {
    "saturation": ("OVERFLOW", "NONLINEAR", "BLOOM"),
    "source": ("SOURCE",),
    "transient": (
        "TRANSIENT",
        "PERSIST",
        "OUTLIER",
        "SNOWBALL",
        "HALO",
        "CROSSTALK",
        "STREAK",
        "GHOST",
        "GHOST_FPA",
        "GHOST_EXT",
    ),
    "bad_pixel": ("SUR_ERROR", "PHANTOM", "REFERENCE", "NONFUNC", "DICHROIC", "MISSING_DATA", "HOT", "COLD", "PHANMISS"),
}


def table_from_header(h: fits.Header) -> dict[str, int]:
    """{name: bit} from the file's own cards, or {} when it has none."""
    bits = {k[3:]: int(h[k]) for k in h if k.startswith("MP_")}
    if bits:
        return bits
    for k in h:
        if k.startswith("MSKN") and k[4:].isdigit():
            m = h.get("MSKM" + k[4:])
            try:
                mask = int(m)
            except (TypeError, ValueError):
                continue
            if mask > 0 and mask & (mask - 1) == 0:
                bits[str(h[k]).strip()] = mask.bit_length() - 1
    return bits


def resolve(h: fits.Header) -> tuple[dict[str, int], str, dict[str, tuple[int, int]]]:
    """(table, "file" | "builtin", mismatches {name: (builtin bit, file bit)}). A mismatch is reported, the file wins."""
    own = table_from_header(h)
    if not own:
        return dict(BUILTIN), "builtin", {}
    mismatches = {n: (BUILTIN[n], b) for n, b in own.items() if n in BUILTIN and BUILTIN[n] != b}
    return own, "file", mismatches


def mask_of(table: Mapping[str, int], names: Iterable[str]) -> int:
    """The bit mask of the named flags that exist in this table (a name the file does not define contributes nothing)."""
    m = 0
    for n in names:
        b = table.get(n)
        if b is not None:
            m |= 1 << b
    return m
