"""The product store: immutable, versioned results on disk with a SQLite catalogue (docs/plan/05-architecture.md 5.6).

    store/products/<kind>/<version>/<key[:2]>/<key>/meta.json + part files

Every write is atomic. A product is `complete` only when all its parts are written; only a complete product is served as immutable.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from service.products.versions import VERSIONS, lineage
from service.storage import atomic_write_bytes, digest

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  key TEXT PRIMARY KEY, kind TEXT NOT NULL, version TEXT NOT NULL, params TEXT NOT NULL,
  created REAL NOT NULL, accessed REAL NOT NULL, bytes INTEGER NOT NULL DEFAULT 0,
  complete INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS products_kind ON products(kind, version);
"""


def product_key(kind: str, params: dict[str, Any], extra: tuple[str, ...] = ()) -> str:
    """Key = hash of the kind, the canonical parameters, the version lineage and any extra inputs (e.g. index cell versions)."""
    canon = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return digest(kind, canon, lineage(kind), extra)[:24]


@dataclass(frozen=True)
class Product:
    key: str
    kind: str
    version: str
    path: Path
    complete: bool


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(root / "catalogue.sqlite", check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    def path(self, kind: str, key: str, version: str | None = None) -> Path:
        return self.root / "products" / kind / (version or VERSIONS[kind]) / key[:2] / key

    def begin(self, kind: str, key: str, params: dict[str, Any]) -> Product:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO products(key, kind, version, params, created, accessed) VALUES (?,?,?,?,?,?)",
                (key, kind, VERSIONS[kind], json.dumps(params, sort_keys=True), now, now),
            )
        p = self.path(kind, key)
        p.mkdir(parents=True, exist_ok=True)
        return Product(key, kind, VERSIONS[kind], p, self.is_complete(key))

    def write_part(self, kind: str, key: str, name: str, data: bytes) -> None:
        atomic_write_bytes(self.path(kind, key) / name, data)
        with self._lock:
            self._db.execute("UPDATE products SET bytes = bytes + ? WHERE key = ?", (len(data), key))

    def write_meta(self, kind: str, key: str, meta: dict[str, Any]) -> None:
        self.write_part(kind, key, "meta.json", json.dumps(meta, separators=(",", ":")).encode())

    def complete(self, key: str) -> None:
        with self._lock:
            self._db.execute("UPDATE products SET complete = 1 WHERE key = ?", (key,))

    def is_complete(self, key: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT complete FROM products WHERE key = ?", (key,)).fetchone()
        return bool(row and row[0])

    def read_part(self, kind: str, key: str, name: str) -> bytes | None:
        p = self.path(kind, key) / name
        if not p.is_file():
            return None
        with self._lock:
            self._db.execute("UPDATE products SET accessed = ? WHERE key = ?", (time.time(), key))
        return p.read_bytes()

    def read_meta(self, kind: str, key: str) -> dict[str, Any] | None:
        b = self.read_part(kind, key, "meta.json")
        return json.loads(b) if b is not None else None

    def pin(self, key: str, pinned: bool = True) -> None:
        with self._lock:
            self._db.execute("UPDATE products SET pinned = ? WHERE key = ?", (int(pinned), key))

    def purge_stale(self) -> dict[str, int]:
        """Delete every product whose version is not current (whole version folders); returns {kind: products removed}."""
        removed: dict[str, int] = {}
        with self._lock:
            rows = self._db.execute("SELECT kind, version, COUNT(*) FROM products GROUP BY kind, version").fetchall()
        for kind, version, n in rows:
            if VERSIONS.get(kind) == version:
                continue
            shutil.rmtree(self.root / "products" / kind / version, ignore_errors=True)
            with self._lock:
                self._db.execute("DELETE FROM products WHERE kind = ? AND version = ?", (kind, version))
            removed[kind] = removed.get(kind, 0) + int(n)
        for kind_dir in (self.root / "products").glob("*") if (self.root / "products").is_dir() else []:
            for vdir in kind_dir.glob("*"):
                if VERSIONS.get(kind_dir.name) != vdir.name:
                    shutil.rmtree(vdir, ignore_errors=True)
        return removed

    def listing(self, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
        """The current-version products of these kinds: key, kind, params, created (epoch s), complete."""
        marks = ",".join("?" for _ in kinds)
        with self._lock:
            rows = self._db.execute(
                f"SELECT key, kind, params, created, complete, version FROM products WHERE kind IN ({marks}) ORDER BY created", kinds
            ).fetchall()
        return [
            {"key": k, "kind": kind, "params": json.loads(p), "created": c, "complete": bool(done)}
            for k, kind, p, c, done, v in rows
            if VERSIONS.get(kind) == v
        ]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, version, COUNT(*), SUM(bytes), SUM(complete), SUM(pinned) FROM products GROUP BY kind, version"
            ).fetchall()
        return {f"{k}/{v}": {"products": n, "bytes": b or 0, "complete": c or 0, "pinned": p or 0} for k, v, n, b, c, p in rows}
