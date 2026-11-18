"""The snapshot's warm-set list (service/snapshot.py): every file of the pinned, complete, current products; the databases are not in
it (deploy/snapshot.sh sends them on their own, each by its own path)."""

from __future__ import annotations

from pathlib import Path

from service.products.store import Store
from service.snapshot import pinned_files


def test_the_list_holds_the_pinned_products_files_only(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for key, pin in (("p" * 24, True), ("u" * 24, False)):
        store.begin("field", key, {"ra": 1.0, "dec": 2.0, "fov": 10.0})
        store.write_part("field", key, "picture_p0.sxp", b"x")
        store.write_meta("field", key, {"passes": [{}]})
        store.complete(key)
        store.pin(key, pin)
    got = pinned_files(tmp_path)
    assert sorted(Path(p).name for p in got) == ["meta.json", "picture_p0.sxp"] and all("p" * 24 in p for p in got)
    assert not any(p.endswith(".sqlite") for p in got)
