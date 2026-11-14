"""Rewrite data/regression/r_dra_deep.json from IRSA's SIA service (run it when a new deep sequence is released).

uv run python tools/extras_snapshot_rdra.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx

from engine.extras import regression_field


async def main() -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0), headers={"User-Agent": "spherex-sky/0.1"}) as client:
        found = await regression_field.fetch(client)
    data = regression_field.compact(found, datetime.now(UTC).date().isoformat())
    regression_field.SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    regression_field.SNAPSHOT.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    for coll, rows in found.items():
        print(coll, len(rows), "files")


if __name__ == "__main__":
    asyncio.run(main())
