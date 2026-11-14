"""Which Gaia release the service asks (one place for it): DR3 today; DR4 (due 2 December 2026, positions at J2017.5, new source
identifiers) by setting SKY_GAIA_TABLE, SKY_GAIA_EPOCH and SKY_GAIA_NAME in the service's environment, no code change. The table must
have the DR3 column names used here (source_id, ra, dec, pmra, pmdec, parallax, parallax_error, ruwe, phot_g_mean_mag, bp_rp).
"""

from __future__ import annotations

import os

TABLE = os.environ.get("SKY_GAIA_TABLE", "gaiadr3.gaia_source")
EPOCH = float(os.environ.get("SKY_GAIA_EPOCH", "2016.0"))
NAME = os.environ.get("SKY_GAIA_NAME", "Gaia DR3")
TAP = os.environ.get("SKY_GAIA_TAP", "https://gea.esac.esa.int/tap-server/tap/sync")
