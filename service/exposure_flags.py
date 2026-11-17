"""The exposures people agreed are broken (service/accounts/people.bad_exposures: called broken by BAD_FLAGS distinct people), left
out of every stack and every spectrum.

The version rule: a field is keyed by the exposures it is built from (service/workers/fieldjob.exposure_signature). The broken
exposures that touch a field are part of that signature, so a new flag changes the key of the fields it touches and of no other:
those are built again without it (the warm set rebuilds its own by itself), every other field keeps its key and its cache. A
spectrum is not measured again: its points carry their exposure's key, and a broken one is marked left out when it is served.

The set lives in this module (the app refreshes it every minute from the accounts database); `version()` changes with it, so
the signatures kept in memory are made again.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_bad: frozenset[str] = frozenset()
_version = 0

BROKEN_WHY = "left out: people agreed this exposure is broken"
NEIGHBOUR_WHY = "left out: people agreed a neighbour star touches it here"


def bad() -> frozenset[str]:
    return _bad


def version() -> int:
    return _version


def set_bad(keys: set[str] | frozenset[str]) -> bool:
    """Replace the set; True when it changed (the version moves on)."""
    global _bad, _version
    new = frozenset(keys)
    with _lock:
        if new == _bad:
            return False
        _bad, _version = new, _version + 1
    return True


def refresh(st: Any) -> bool:
    """Read the set from the accounts database (call it in a thread). False when it did not change or there are no accounts."""
    try:
        from service.accounts import people

        return set_bad(people.bad_exposures(st))
    except Exception:  # no accounts here: nothing is left out
        return False


def mark_points(points: list[dict[str, Any]], neighbour: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The points of a spectrum with the broken exposures (and, for one find, those people saw a neighbour touch) not ok, with why."""
    out = []
    for p in points:
        k = p.get("key")
        if k in _bad and p.get("ok"):
            p = {**p, "ok": False, "why": BROKEN_WHY}
        elif k in neighbour and p.get("ok"):
            p = {**p, "ok": False, "why": NEIGHBOUR_WHY}
        out.append(p)
    return out
