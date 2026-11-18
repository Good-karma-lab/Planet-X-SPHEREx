"""List the files of a snapshot's warm set: every file of the pinned (warm-set) products, relative to the store (the databases go up
on their own, as dated copies: deploy/snapshot.sh).

python -m service.snapshot list STORE   -> one relative path per line
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from service.products.versions import VERSIONS


def pinned_files(store: Path) -> list[str]:
    db = sqlite3.connect(f"file:{store / 'catalogue.sqlite'}?mode=ro", uri=True)
    out: list[str] = []
    for key, kind, version in db.execute("SELECT key, kind, version FROM products WHERE pinned = 1 AND complete = 1"):
        if VERSIONS.get(kind) != version:
            continue
        d = store / "products" / kind / version / key[:2] / key
        out += [str(p.relative_to(store)) for p in sorted(d.iterdir()) if p.is_file()]
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "list":
        print(__doc__)
        return 2
    print("\n".join(pinned_files(Path(argv[2]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
