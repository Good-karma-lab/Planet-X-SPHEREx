"""Verdicts and reviewers on the server.

python -m service.accounts.verdict <finding_id> <status> [note]   the verdict on a finding; everyone credited on it is told
python -m service.accounts.verdict --grant <e-mail>               make that account a reviewer (it has signed in once)
python -m service.accounts.verdict --revoke <e-mail>
python -m service.accounts.verdict --reviewers                    who the reviewers are (names only)
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from service.accounts.db import AccountsDB
from service.accounts.review import REVIEW_STATUSES, Review
from service.accounts.score import Score
from service.config import load
from service.storage import product_db_path


def main(argv: list[str]) -> int:
    db = AccountsDB(product_db_path(load().store_dir))
    rv = Review(db)
    if len(argv) == 2 and argv[0] in ("--grant", "--revoke"):
        ok = rv.grant(argv[1]) if argv[0] == "--grant" else rv.revoke(argv[1])
        print(("granted" if argv[0] == "--grant" else "revoked") if ok else "no such account (it must have signed in once)")
        return 0 if ok else 1
    if argv == ["--reviewers"]:
        for r in db._conn().execute("SELECT u.name FROM acc_reviewers r JOIN acc_users u ON u.id=r.user_id ORDER BY r.granted"):
            print(r["name"])
        return 0
    if len(argv) not in (2, 3) or argv[1] not in REVIEW_STATUSES:
        print(__doc__)
        print(f"status: {'|'.join(REVIEW_STATUSES)}")
        return 2
    try:
        told = rv.give(argv[0], argv[1], None, argv[2] if len(argv) == 3 else "", Score(SimpleNamespace(), db))
    except KeyError:
        print(f"{argv[0]}: no such submitted finding")
        return 1
    print(f"{argv[0]}: {argv[1]}; {told} people notified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
