"""Development only (stream F3): a private store whose findings are a snapshot of a running service's real findings.

    uv run python tools/findings_snapshot.py --shared ../Planet-X-SPHEREx/store --out .store [--url https://www.spherex.space]

`--out` gets a link to everything in the shared store (fields, index, raw files: read through the links) and its OWN findings.db, filled
from `GET <url>/api/v1/findings` (the finds, what each catalogue said, positions, spectra, light curves). Run the service on it with
SKY_STANDBY=1 so that the sweep does not replace the snapshot with the few finds of the local result files.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from service.findings import FindingsDB


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shared", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--url", default="https://www.spherex.space")
    ap.add_argument("--json", type=Path, help="a saved answer of /api/v1/findings instead of asking --url")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for p in a.shared.resolve().iterdir():
        if p.name.startswith("findings.db") or p.name == "clips":
            continue
        link = a.out / p.name
        if not link.exists():
            link.symlink_to(p)
    finds: list[dict] = []
    if a.json:
        finds = json.loads(a.json.read_text())["finds"]
    else:
        for status in ("all", "residual", "galaxy", "below", "halo"):
            with urllib.request.urlopen(f"{a.url}/api/v1/findings?limit=2000&status={status}", timeout=120) as r:
                finds += json.load(r)["finds"]
    for old in a.out.glob("findings.db*"):
        old.unlink()
    db = FindingsDB(a.out / "findings.db", writer=True)
    db.write_finds(finds)
    for f in finds:
        for i in f.get("idents") or []:
            if i.get("state") and i["state"] != "pending":
                db.put_ident(f["id"], i["catalogue"], i["state"], i.get("name"), i.get("sep"), i.get("type"), i.get("detail") or {})
    db.db.commit()
    print(f"{len(finds)} finds written to {a.out / 'findings.db'}")


if __name__ == "__main__":
    main()
