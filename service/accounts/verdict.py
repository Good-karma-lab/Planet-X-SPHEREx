"""Set the verdict on a finding and tell everyone credited on it:  python -m service.accounts.verdict <finding_id> <status>"""

from __future__ import annotations

import sys

from service.accounts.db import STATUSES, AccountsDB
from service.config import load
from service.storage import product_db_path


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in STATUSES:
        print(f"usage: python -m service.accounts.verdict <finding_id> <{'|'.join(STATUSES)}>")
        return 2
    told = AccountsDB(product_db_path(load().store_dir)).set_status(argv[0], argv[1])
    print(f"{argv[0]}: {argv[1]}; {told} people notified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
