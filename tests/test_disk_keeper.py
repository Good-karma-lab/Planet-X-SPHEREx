"""The disk keeper on a fake store: tmp, backups, stale clips, raw bands least-recently-used first, fields only when raw is small,
never a protected field; dry run deletes nothing."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import disk_keeper as dk

from service.products.store import Store
from service.products.versions import VERSIONS

MB = 1_000_000


def put(p: Path, size: int, age_s: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = time.time() - age_s
    os.utime(p, (t, t))


def make_store(root: Path) -> None:
    put(root / "tmp" / "old.part", 1 * MB, 3 * 86400)
    put(root / "tmp" / "new.part", 1 * MB, 60)
    for i in range(4):  # raw bands: i=0 oldest
        put(root / "raw" / "bands" / "ab" / f"{i}.npz", 10 * MB, (10 - i) * 86400)
    put(root / "raw" / "bands" / "ab" / "busy.npz", 10 * MB, 10)  # used moments ago
    put(root / "raw" / "calib" / "cal.npz", 1 * MB, 30 * 86400)
    with sqlite3.connect(root / "findings.db") as c:
        c.execute("CREATE TABLE finds (id TEXT PRIMARY KEY, ra REAL, dec REAL)")
        c.execute("INSERT INTO finds VALUES ('mover:keep', 10.0, 10.0)")
    put(root / "clips" / "c9" / "ep" / "mover_keep.webp", 1000, 100)
    put(root / "clips" / "c9" / "ne" / "mover_gone.webp", 1000, 100)
    put(root / "clips" / "c9" / "ne" / "mover_gone_still.webp", 1000, 100)
    put(root / "clips" / "c9" / "pl" / "place_1.0000_+2.0000_s320.webp", 1000, 100)
    put(root / "clips" / "c5" / "aa" / "mover_keep.webp", 1000, 100)
    st = Store(root)
    for key, ra, dec in (("a" * 24, 10.0, 10.0), ("b" * 24, 50.0, -20.0), ("c" * 24, 100.0, 30.0)):
        st.begin("field", key, {"ra": ra, "dec": dec, "fov": 5.0})
        st.write_part("field", key, "meta.json", json.dumps({"ok": 1}).encode() + b" " * (5 * MB))
        st.complete(key)
    st._db.execute("UPDATE products SET accessed = ? WHERE kind='field'", (time.time() - 5 * 86400,))
    st.pin("c" * 24)
    st.close()


def args(root: Path, backups: Path, **kw: object) -> argparse.Namespace:
    base = dict(
        store=str(root), clips=None, clip_version="c9", backups=str(backups), high=75.0, low=60.0, raw_floor=5.0, busy_hours=1.0,
        alert_pct=85.0, mail_to="x@example.org", dry_run=False, force=False,
    )  # fmt: skip
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "sky"
    make_store(root)
    backups = tmp_path / "backup"
    for i in range(9):
        (backups / f"2026111{i}T0000Z").mkdir(parents=True)
        (backups / f"2026111{i}T0000Z" / "f").write_bytes(b"y" * 100)
    return root, backups


def fill(monkeypatch: pytest.MonkeyPatch, root: Path, total: int, base_used: int) -> None:
    """The disk is `total` bytes; its use is `base_used` plus what is left in the store."""

    def usage(path: Path) -> tuple[float, float, int]:
        left = sum(e.stat().st_size for e in dk.walk_files(root))
        used = base_used + left
        return 100.0 * used / total, (total - used) / 1e9, total

    monkeypatch.setattr(dk, "usage", usage)


def test_dry_run_deletes_nothing(store: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    root, backups = store
    fill(monkeypatch, root, total=100 * MB, base_used=40 * MB)  # about 100% full
    before = sorted(str(e.path) for e in dk.walk_files(root))
    rep = dk.run(args(root, backups, dry_run=True))
    assert sorted(str(e.path) for e in dk.walk_files(root)) == before and len(list(backups.iterdir())) == 9
    assert rep.freed["tmp"] == 1 * MB and rep.files["backups"] == 2 and rep.freed["raw/bands"] > 0


def test_a_real_run(store: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    root, backups = store
    fill(monkeypatch, root, total=100 * MB, base_used=40 * MB)
    rep = dk.run(args(root, backups, raw_floor=0.001))  # 1 MB: the busy band left is not "small"
    assert not (root / "tmp" / "old.part").exists() and (root / "tmp" / "new.part").exists()
    assert len(list(backups.iterdir())) == 7 and (backups / "20261118T0000Z").exists() and not (backups / "20261110T0000Z").exists()
    assert (root / "clips" / "c9" / "ep" / "mover_keep.webp").exists() and not (root / "clips" / "c9" / "ne" / "mover_gone.webp").exists()
    assert not (root / "clips" / "c9" / "ne" / "mover_gone_still.webp").exists()
    assert (root / "clips" / "c9" / "pl" / "place_1.0000_+2.0000_s320.webp").exists() and not (root / "clips" / "c5").exists()
    bands = root / "raw" / "bands" / "ab"
    assert not (bands / "0.npz").exists() and (bands / "busy.npz").exists()  # the oldest went first, the busy one stays
    assert (root / "raw" / "calib" / "cal.npz").exists() and (root / "findings.db").exists()
    # raw/bands was not small: no field is touched
    assert "products/field" not in rep.freed


def test_fields_only_when_raw_is_small_and_never_protected(store: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    root, backups = store
    fill(monkeypatch, root, total=40 * MB, base_used=10 * MB)  # still full after raw: fields go
    dk.run(args(root, backups, raw_floor=100.0))
    with sqlite3.connect(root / "catalogue.sqlite") as c:
        keys = {r[0] for r in c.execute("SELECT key FROM products WHERE kind='field'")}
    v = VERSIONS["field"]
    assert "a" * 24 in keys and (root / "products" / "field" / v / "aa" / ("a" * 24)).is_dir()  # holds a find
    assert "c" * 24 in keys  # pinned (the warm set)
    assert "b" * 24 not in keys and not (root / "products" / "field" / v / "bb" / ("b" * 24)).exists()


def test_alert_is_sent_once_a_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []

    class R:
        status_code = 200

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: sent.append(k["json"]) or R())
    monkeypatch.setenv("SKY_RESEND_API_KEY", "re_test")
    stamp = tmp_path / "last_alert"
    assert dk.alert("x@example.org", "s", "t", stamp, dry_run=False) == "sent"
    assert dk.alert("x@example.org", "s", "t", stamp, dry_run=False) == "already sent today" and len(sent) == 1
    assert dk.alert("x@example.org", "s", "t", tmp_path / "other", dry_run=True) == "dry run: no mail"
