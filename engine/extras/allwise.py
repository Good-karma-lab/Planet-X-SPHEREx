"""AllWISE (and the 2MASS magnitudes it carries) for a place, from IRSA's Parquet copy of the catalogue in AWS.

AllWISE's own 2MASS association gives J, H, Ks without another catalogue. The catalogue's epoch is 2010.5 (the mean of each band's
observations is in `w1mjdmean` ... ): a moving source is looked for at its position at that epoch. Bucket and layout:
https://irsa.ipac.caltech.edu/cloud_access/ (AllWISE, "HEALPix Parquet").
"""

from __future__ import annotations

from typing import Any

from engine.extras.cloudcat import HealpixDataset, Reader, cone, s3_reader

BUCKET = "nasa-irsa-wise"
REGION = "us-west-2"
DATASET = HealpixDataset(f"{BUCKET}/wise/allwise/catalogs/p3as_psd/healpix_k5/wise-allwise.parquet")
EPOCH_MJD = 51544.5 + 10.5 * 365.25  # 2010.5, as the other epochs of the service are computed
COLUMNS = [
    "designation", "w1mpro", "w1sigmpro", "w1snr", "w2mpro", "w2sigmpro", "w2snr", "w3mpro", "w3sigmpro", "w4mpro", "w4sigmpro",
    "ph_qual", "cc_flags", "ext_flg", "var_flg", "nb", "na", "pmra", "sigpmra", "pmdec", "sigpmdec", "w1mjdmean", "w2mjdmean",
    "tmass_key", "j_m_2mass", "j_msig_2mass", "h_m_2mass", "h_msig_2mass", "k_m_2mass", "k_msig_2mass",
]  # fmt: skip


def default_reader() -> Reader:
    return s3_reader(REGION)


def sources(ra: float, dec: float, radius_arcsec: float = 3.0, reader: Reader | None = None) -> list[dict[str, Any]]:
    """AllWISE sources within `radius_arcsec` of a position at the catalogue's epoch, nearest first."""
    return cone(DATASET, reader or default_reader(), COLUMNS, ra, dec, radius_arcsec)


def photometry(row: dict[str, Any]) -> dict[str, dict[str, float | str | None]]:
    """The catalogue magnitudes of one source by band (W1-W4 with the quality letter of the band, J H Ks from the 2MASS association)."""
    qual = str(row.get("ph_qual") or "")
    out: dict[str, dict[str, float | str | None]] = {}
    for i, band in enumerate(("W1", "W2", "W3", "W4")):
        key = f"w{i + 1}"
        out[band] = {"mag": row.get(f"{key}mpro"), "err": row.get(f"{key}sigmpro"), "qual": qual[i : i + 1] or None}
    for band, key in (("J", "j"), ("H", "h"), ("Ks", "k")):
        # the 2MASS association has no quality letter here: a magnitude without an error is an upper limit
        err = row.get(f"{key}_msig_2mass")
        out[band] = {"mag": row.get(f"{key}_m_2mass"), "err": err, "qual": None if err is not None else "U"}
    return out
