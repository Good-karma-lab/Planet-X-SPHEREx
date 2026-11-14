"""An MPC ADES (Astrometry Data Exchange Standard) file, pipe-separated, for the positions of one mover lead.

Format: https://minorplanetcenter.net/iau/info/ADES.html . SPHEREx is a spacecraft with MPC observatory code 288; the MPC wants the
observer's position for every observation of a spacecraft (`sys`, `ctr`, `pos1`..`pos3`), so a file without positions is built but
reported as not ready. The checks below list what the MPC's validator would refuse; nothing is sent anywhere.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

OBSERVATORY_CODE = "288"  # SPHEREx, MPC list of observatory codes
MODES = {"CCD", "CMOS", "PHO", "VID", "ENC", "TDI", "OCC", "UNK"}
BANDS = {
    "B",
    "V",
    "R",
    "I",
    "J",
    "H",
    "K",
    "Y",
    "G",
    "g",
    "r",
    "i",
    "z",
    "w",
    "c",
    "o",
    "u",
    "Bj",
    "Vj",
    "Rc",
    "Ic",
    "Ja",
    "Ha",
    "Ka",
    "Ya",
}
TRK_SUB = re.compile(r"^[A-Za-z0-9]{1,8}$")
MJD_UNIX_EPOCH = datetime(1858, 11, 17, tzinfo=UTC)


@dataclass(frozen=True)
class Observation:
    mjd_utc: float  # middle of the exposure, UTC
    ra: float  # degrees, ICRS
    dec: float
    rms_ra_arcsec: float  # of RA * cos(dec)
    rms_dec_arcsec: float
    mag: float | None = None
    rms_mag: float | None = None
    band: str | None = None
    position_km: tuple[float, float, float] | None = None  # spacecraft, ICRF, geocentric


@dataclass(frozen=True)
class Header:
    submitter: str
    observers: list[str] = field(default_factory=list)
    measurers: list[str] = field(default_factory=list)
    institution: str = ""
    ast_cat: str = "UNK"
    mode: str = "UNK"
    detector: str = "H2RG infrared array"
    design: str = "Cryogenic telescope"
    aperture_m: float = 0.2
    comment: str = ""


def iso_time(mjd: float) -> str:
    t = MJD_UNIX_EPOCH + timedelta(days=mjd)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def problems(track_id: str, obs: list[Observation], header: Header) -> list[str]:
    """What the MPC would refuse, or what is missing before the file can be sent (an empty list: ready)."""
    out: list[str] = []
    if not TRK_SUB.match(track_id):
        out.append("trkSub must be 1 to 8 letters or digits")
    if len(obs) < 2:
        out.append("a tracklet needs at least two observations")
    if not header.submitter.strip():
        out.append("submitter name is missing")
    if not header.observers or not header.measurers:
        out.append("observers and measurers need at least one name each")
    if header.mode not in MODES:
        out.append(f"mode must be one of {sorted(MODES)}")
    if header.ast_cat == "UNK":
        out.append("astCat is UNK: name the catalogue the positions were reduced against (for example Gaia3)")
    for i, o in enumerate(obs):
        tag = f"observation {i + 1}"
        if not (0.0 <= o.ra < 360.0 and -90.0 <= o.dec <= 90.0):
            out.append(f"{tag}: position out of range")
        if not (math.isfinite(o.rms_ra_arcsec) and o.rms_ra_arcsec > 0 and math.isfinite(o.rms_dec_arcsec) and o.rms_dec_arcsec > 0):
            out.append(f"{tag}: the position uncertainties must be positive")
        if o.position_km is None:
            out.append(f"{tag}: the spacecraft position is missing (required for a spacecraft)")
        if o.mag is not None and (o.band is None or o.band not in BANDS):
            out.append(f"{tag}: a magnitude needs a band from the ADES list")
    times = [o.mjd_utc for o in obs]
    if times != sorted(times):
        out.append("observations must be in time order")
    return out


def _cell(v: object, width: int) -> str:
    return str(v).ljust(width)


def to_psv(track_id: str, obs: list[Observation], header: Header) -> str:
    """The ADES PSV text: the header blocks, the column line, one line per observation (positions of the spacecraft when all are known)."""
    lines = ["# version=2022", "# observatory", f"! mpcCode {OBSERVATORY_CODE}", "# submitter", f"! name {header.submitter}"]
    if header.institution:
        lines.append(f"! institution {header.institution}")
    lines += ["# observers", *[f"! name {n}" for n in header.observers], "# measurers", *[f"! name {n}" for n in header.measurers]]
    lines += ["# telescope", f"! design {header.design}", f"! aperture {header.aperture_m:g}", f"! detector {header.detector}"]
    if header.comment:
        lines += ["# comment", f"! line {header.comment}"]
    with_pos = all(o.position_km is not None for o in obs)
    with_mag = any(o.mag is not None for o in obs)
    cols = [("trkSub", 8), ("mode", 4), ("stn", 4), ("obsTime", 24), ("ra", 12), ("dec", 12), ("rmsRA", 7), ("rmsDec", 7), ("astCat", 7)]
    if with_mag:
        cols += [("mag", 6), ("rmsMag", 6), ("band", 4)]
    if with_pos:
        cols += [("sys", 7), ("ctr", 4), ("pos1", 14), ("pos2", 14), ("pos3", 14)]
    lines.append("|".join(_cell(n, w) for n, w in cols).rstrip())
    for o in obs:
        vals: list[object] = [
            track_id,
            header.mode,
            OBSERVATORY_CODE,
            iso_time(o.mjd_utc),
            f"{o.ra:.7f}",
            f"{o.dec:.7f}",
            f"{o.rms_ra_arcsec:.3f}",
            f"{o.rms_dec_arcsec:.3f}",
            header.ast_cat,
        ]
        if with_mag:
            vals += [f"{o.mag:.2f}" if o.mag is not None else "", f"{o.rms_mag:.2f}" if o.rms_mag is not None else "", o.band or ""]
        if with_pos:
            assert o.position_km is not None
            vals += ["ICRF_KM", "399", *[f"{c:.4f}" for c in o.position_km]]
        lines.append("|".join(_cell(v, w) for v, (_, w) in zip(vals, cols, strict=True)).rstrip())
    return "\n".join(lines) + "\n"
