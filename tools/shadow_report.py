"""The daily report of the shadow mode (service/shadow.py). Reads only: shadow.db, findings.db and the answers' counts.

uv run python tools/shadow_report.py --store /var/lib/sky [--product /var/lib/sky/product.sqlite] [--json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from service import shadow
from service.storage import product_db_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--product", type=Path, help="the product database (people's answers); default beside the store")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rep = shadow.report(a.store, a.product or product_db_path(a.store))
    if a.json:
        print(json.dumps(rep, indent=1))
        return
    print(f"rules {rep['rule_version']}, day {rep['day']} of 14, rows {rep['rows_all']}")
    for kind, word in (("film", "tracks"), ("peak", "peaks")):
        r = rep[kind]
        print(f"{word}: {r['finds']}, judged {r['judged']}, would hide {r['would_hide']}; last day {r['last_day']}")
        print(f"  inputs missing: {r['missing']}")
        print(f"  named by JPL: {r['jpl_known']['judged']}, of them would hide {r['jpl_known']['would_hide']}")
        print(f"  two people said real: {r['people_real']}; a person said empty: {r['people_empty']}")
    print("stop rules:", "; ".join(rep["stop"]) or "none")


if __name__ == "__main__":
    main()
