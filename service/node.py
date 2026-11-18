"""A second node that searches squares of the sky grid for the main service (deploy/MIGRATE.md, "A second node").

The main service lends squares; the node searches each one locally (its own reads of the public archive, its own field and searches)
and sends back only what the main service keeps of a square after its own sweep: the products' rows and metas (the 20′ field
stripped of its pictures, as the main sweep strips it) and the result files of the searches. The main service writes them under the
same paths, marks the square searched, and its Findings collector reads them as if it had built them. Nothing is shared but this:
each node keeps its own databases; people's requests stay on the main service.

Calls go one way, node -> main, over the public address (the main service never opens a connection to the node). Each request is
signed: HMAC-SHA256 of "<unix time>.<body>" with the secret both hold (`SKY_NODE_SECRET`), in `X-Sky-Node` as "<time>.<hex>";
older than NODE_SKEW_S or a wrong signature is refused.

  main (SKY_NODE_SECRET set):   POST /api/internal/lease   {node}            -> {square: {id, ra, dec} | null, lease_s}
                                POST /api/internal/result  {node, square, row, products: [...], files: [...]} -> {ok}
  node (SKY_NODE_MAIN = the main service's address, SKY_NODE_SECRET): the sweep takes its squares from the lease and sends each back.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path, PurePosixPath
from typing import Any

from service.storage import atomic_write_bytes

NODE_SKEW_S = 300.0
LEASE_S = 2 * 3600.0  # a square not sent back by then is lent again (the node died or gave up)
FILE_MAX_BYTES = 8_000_000  # one result file
RESULT_DIRS = ("movers", "pairs", "browndwarfs", "red", "anomalies")  # the searches' result files (service/findings.collect reads them)
PRODUCT_KINDS = ("field", "filmsearch", "distant", "deep")  # rows and metas only: the pictures stay on the node; a film is made
# again on the main service when someone opens it (a stripped film row would say it is there)

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_leases (
  cell_id TEXT PRIMARY KEY,
  node TEXT NOT NULL,
  until REAL NOT NULL,
  given REAL NOT NULL
);
"""


def secret() -> str:
    return os.environ.get("SKY_NODE_SECRET", "")


def sign(key: str, body: bytes, now: float | None = None) -> str:
    t = str(int(now if now is not None else time.time()))
    mac = hmac.new(key.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"{t}.{mac}"


def verify(key: str, body: bytes, header: str | None, now: float | None = None) -> bool:
    if not key or not header or "." not in header:
        return False
    t, _, mac = header.partition(".")
    try:
        age = abs((now if now is not None else time.time()) - float(t))
    except ValueError:
        return False
    if age > NODE_SKEW_S:
        return False
    good = hmac.new(key.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, mac)


# ---- main ---------------------------------------------------------------------------------------------------------------------------


class Leases:
    """The squares lent to nodes (the product database, beside the grid)."""

    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10.0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=8000")
        self.db.executescript(SCHEMA)

    def lend(self, grid_db: Any, order: Any, node: str, now: float | None = None) -> Any:
        """The next square in the sweep order nobody searched, is searching or holds (an expired lease is taken back first)."""
        t = now if now is not None else time.time()
        for r in self.db.execute("SELECT cell_id FROM node_leases WHERE until < ?", (t,)).fetchall():
            row = grid_db.get(r["cell_id"])
            if row is not None and row.get("state") == "searching" and str(row.get("source") or "").startswith("node:"):
                grid_db.put(r["cell_id"], "new", source="sweep")
            self.db.execute("DELETE FROM node_leases WHERE cell_id = ?", (r["cell_id"],))
        for ce in order:
            row = grid_db.get(ce.id)
            if row is not None and row.get("state") in ("searched", "failed", "searching"):
                continue
            self.db.execute("INSERT OR REPLACE INTO node_leases VALUES (?,?,?,?)", (ce.id, node, t + LEASE_S, t))
            grid_db.put(ce.id, "searching", source=f"node:{node}")
            return ce
        return None

    def holds(self, cell_id: str, node: str) -> bool:
        r = self.db.execute("SELECT node FROM node_leases WHERE cell_id = ?", (cell_id,)).fetchone()
        return r is not None and r["node"] == node

    def release(self, cell_id: str) -> None:
        self.db.execute("DELETE FROM node_leases WHERE cell_id = ?", (cell_id,))


def safe_rel(path: str) -> PurePosixPath | None:
    """A result file's path relative to the store, or None when it is not one of RESULT_DIRS/<name>.json (no '..', no absolute)."""
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or len(p.parts) != 2 or p.parts[0] not in RESULT_DIRS or not p.name.endswith(".json"):
        return None
    return p


def apply_result(store: Any, store_dir: Path, grid_db: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Write what a node sends back for a square: the products' rows and metas (each marked stripped: the pictures stay on the node),
    the result files, and the square's grid row. -> {products, files}. Raises ValueError on anything outside what a square makes."""
    from service.products.versions import VERSIONS

    n_products = n_files = 0
    for p in body.get("products") or []:
        kind, key, version = str(p.get("kind")), str(p.get("key")), str(p.get("version"))
        if kind not in PRODUCT_KINDS or not key.isalnum() or len(key) > 64:
            raise ValueError(f"product {kind}:{key} is not one a square makes")
        if VERSIONS.get(kind) != version:
            continue  # made by another version: this service would not read it
        if store.is_complete(key):
            continue  # this service has it already (it built it itself, or a node sent it before)
        store.begin(kind, key, p.get("params") or {})
        store.write_meta(kind, key, {**(p.get("meta") or {}), "stripped": True, "made_on": body.get("node")})
        store.complete(key)
        n_products += 1
    for f in body.get("files") or []:
        rel = safe_rel(str(f.get("path")))
        text = f.get("text")
        if rel is None or not isinstance(text, str):
            raise ValueError(f"file {f.get('path')} is not a search's result file")
        data = text.encode()
        if len(data) > FILE_MAX_BYTES:
            raise ValueError(f"file {rel} is larger than {FILE_MAX_BYTES} bytes")
        json.loads(text)  # a result file is JSON: anything else is refused
        atomic_write_bytes(store_dir / Path(*rel.parts), data)
        n_files += 1
    row = body.get("row") or {}
    state = row.get("state") if row.get("state") in ("searched", "failed") else "failed"
    grid_db.put(
        str(body["square"]), state, finds=int(row.get("finds") or 0), find_ids=list(row.get("find_ids") or []), bar=row.get("bar") or {},
        field_key=row.get("field_key"), searched_at=row.get("searched_at"), source=f"node:{body.get('node')}",
        seconds=row.get("seconds"), error=row.get("error"),
    )  # fmt: skip
    return {"products": n_products, "files": n_files}


# ---- node ---------------------------------------------------------------------------------------------------------------------------


def main_url() -> str:
    return os.environ.get("SKY_NODE_MAIN", "").rstrip("/")


def node_name() -> str:
    return os.environ.get("SKY_NODE_NAME", "node")


def is_node() -> bool:
    return bool(main_url() and secret())


def call(path: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    """One signed call to the main service (blocking: run it in a thread)."""
    import httpx

    body = json.dumps(payload, separators=(",", ":")).encode()
    r = httpx.post(
        f"{main_url()}{path}", content=body, timeout=timeout,
        headers={"content-type": "application/json", "x-sky-node": sign(secret(), body), "user-agent": f"spherex-sky-node/{node_name()}"},
    )  # fmt: skip
    r.raise_for_status()
    out: dict[str, Any] = r.json()
    return out


def made_since(store: Any, store_dir: Path, since: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What this node made for a square since `since`: the products' rows and metas, and the result files (JSON text)."""
    from service.products.versions import VERSIONS

    products = []
    for row in store.listing(PRODUCT_KINDS):
        if row["created"] < since - 1.0 or not row["complete"]:
            continue
        meta = store.read_meta(row["kind"], row["key"])
        if meta is None:
            continue
        products.append({"kind": row["kind"], "key": row["key"], "version": VERSIONS[row["kind"]], "params": row["params"], "meta": meta})
    files = []
    for d in RESULT_DIRS:
        root = store_dir / d
        if not root.is_dir():
            continue
        for p in root.glob("*.json"):
            try:
                if p.stat().st_mtime >= since - 1.0 and p.stat().st_size <= FILE_MAX_BYTES:
                    files.append({"path": f"{d}/{p.name}", "text": p.read_text()})
            except OSError:
                continue
    return products, files
