"""Horizons commands for the names JPL's Small-Body Identification returns."""

from __future__ import annotations

from service.bodies import horizons_command


def test_numbered_and_provisional_names() -> None:
    assert horizons_command("9078 (1994 PB2)") == "9078;"
    assert horizons_command("7519 Paulcook (1989 UN3)") == "7519;"
    assert horizons_command("(2010 AB12)") == "DES=2010 AB12;"
    assert horizons_command("C/2025 N1 (ATLAS)") == "DES=C/2025 N1;CAP;"
