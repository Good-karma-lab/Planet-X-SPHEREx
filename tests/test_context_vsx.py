"""VSX through VizieR's cone search (service/context.py parse_vsx_tsv): TAPVizieR did not answer for days, so every find's VSX said
"did not answer"; the cone search on two hosts is asked as well. VizieR's own error (its database unreachable) is no answer, not "no
variable here"."""

from __future__ import annotations

import pytest

from service.context import parse_vsx_tsv

ONE = """#
#   VizieR Astronomical Server vizier.cfa.harvard.edu
#RESOURCE=yCat_2027
#Name: B/vsx
#Table\tB_vsx_vsx:
Name\tType\tPeriod\tRAJ2000\tDEJ2000
\t\td\tdeg\tdeg
------------------------------\t--------------------\t---------------\t----------\t----------
RR Lyr\tRRAB/BL\t0.5668378\t291.366292\t+42.784364
V0123 Lyr\tEW\t0.31\t291.369000\t+42.786000
"""

ERROR = """#   VizieR Astronomical Server vizier.cfa.harvard.edu
#INFO\tError=The database is not currently reachable.\t
#INFO\tQUERY_STATUS=ERROR\t
"""

EMPTY = """#   VizieR Astronomical Server vizier.cfa.harvard.edu
#RESOURCE=yCat_2027
"""


def test_the_nearest_vsx_star_is_named() -> None:
    got = parse_vsx_tsv(ONE, 291.3663, 42.7844)
    assert got["state"] == "ok" and got["name"] == "RR Lyr" and got["type"] == "RRAB/BL" and got["sep"] < 1.0


def test_vizier_s_own_error_is_no_answer_and_nothing_found_is_none() -> None:
    with pytest.raises(ValueError, match="not currently reachable"):
        parse_vsx_tsv(ERROR, 291.3663, 42.7844)
    assert parse_vsx_tsv(EMPTY, 291.3663, 42.7844) == {"state": "none"}
