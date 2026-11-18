"""Reviewers and their verdicts on submitted findings.

What the machine settles by itself, without a reviewer (Method part 14, "Who gives a verdict"):
  - a find's status in Findings (known, unexplained, residual, halo, blend…) from the catalogues (service/findings.status_of);
  - a find people set aside as an artefact, once enough of them agree (service/accounts/field_people.set_aside);
  - a mark's verdict (known, no match, set aside) from JPL and the catalogues (service/accounts/field_people);
  - a submit: the checks run, and a finding that passes every one is submitted by the server itself (service/accounts/auto).
What only a reviewer gives: the stage of a submitted finding (in review, known, artefact, candidate, confirmed), which pays the
submitter's candidate and confirmed points, settles every answer that waited for it and tells everyone credited.

A reviewer is an account named in acc_reviewers (granted on the server: python -m service.accounts.verdict --grant <e-mail>). Every
verdict is logged with who gave it and why (acc_verdict_log); the command line's own verdicts are logged as "command line".
"""

from __future__ import annotations

import json
import time
from typing import Any

from service.accounts.db import AccountsDB, iso

REVIEW_STATUSES = ("in_review", "known", "artefact", "candidate", "confirmed")  # "new" and "submitted" are the machine's own
NOTE_MAX = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS acc_reviewers (
  user_id TEXT PRIMARY KEY,
  granted REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS acc_verdict_log (
  finding_id TEXT NOT NULL,
  status TEXT NOT NULL,
  user_id TEXT,
  note TEXT NOT NULL DEFAULT '',
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acc_verdict_log_finding ON acc_verdict_log(finding_id, at);
"""


class Review:
    def __init__(self, db: AccountsDB) -> None:
        self.db = db
        self.db._conn().executescript(SCHEMA)

    def is_reviewer(self, user_id: str | None) -> bool:
        if not user_id:
            return False
        return self.db._conn().execute("SELECT 1 FROM acc_reviewers WHERE user_id=?", (user_id,)).fetchone() is not None

    def grant(self, email: str) -> bool:
        """Make the account of this address a reviewer. False when no account has it (it must have signed in once)."""
        r = self.db._conn().execute("SELECT id FROM acc_users WHERE email=?", (email.strip().lower(),)).fetchone()
        if r is None:
            return False
        self.db._conn().execute("INSERT OR IGNORE INTO acc_reviewers(user_id, granted) VALUES(?,?)", (r["id"], time.time()))
        return True

    def revoke(self, email: str) -> bool:
        c = self.db._conn()
        r = c.execute("SELECT id FROM acc_users WHERE email=?", (email.strip().lower(),)).fetchone()
        return r is not None and c.execute("DELETE FROM acc_reviewers WHERE user_id=?", (r["id"],)).rowcount > 0

    def queue(self) -> dict[str, Any]:
        """The submitted findings waiting for a verdict (the oldest first) and the latest verdicts given. Names only, never an address."""
        c = self.db._conn()
        credits = self.db.credits_of()

        def row(r: Any) -> dict[str, Any]:
            return {
                "finding_id": r["finding_id"],
                "title": r["title"],
                "status": r["status"],
                "ra": r["ra"],
                "dec": r["dec"],
                "field_key": r["field_key"],
                "submitted": iso(r["created"]),
                "updated": iso(r["updated"]),
                "by": r["name"],
                "checks": json.loads(r["checks"] or "[]"),
                "credit": credits.get(r["finding_id"], []),
            }

        q = "SELECT s.*, u.name FROM acc_submissions s LEFT JOIN acc_users u ON u.id=s.user_id WHERE s.status IN ({}) ORDER BY s.created"
        waiting = [row(r) for r in c.execute(q.format("'submitted','in_review'"))]
        log = [
            {"finding_id": r["finding_id"], "status": r["status"], "by": r["name"] or "command line", "note": r["note"], "at": iso(r["at"])}
            for r in c.execute(
                "SELECT l.*, u.name FROM acc_verdict_log l LEFT JOIN acc_users u ON u.id=l.user_id ORDER BY l.at DESC LIMIT 50"
            )
        ]
        return {"waiting": waiting, "log": log, "statuses": list(REVIEW_STATUSES)}

    def give(self, finding_id: str, status: str, reviewer_id: str | None, note: str, score: Any) -> int:
        """A verdict: the finding's stage, its points and settled answers (score.verdict), everyone credited told. Returns how many."""
        if status not in REVIEW_STATUSES:
            raise ValueError(f"status is one of {', '.join(REVIEW_STATUSES)}")
        c = self.db._conn()
        if c.execute("SELECT 1 FROM acc_submissions WHERE finding_id=?", (finding_id,)).fetchone() is None:
            raise KeyError(finding_id)
        told = self.db.set_status(finding_id, status)
        score.verdict(finding_id, status)
        c.execute(
            "INSERT INTO acc_verdict_log(finding_id,status,user_id,note,at) VALUES(?,?,?,?,?)",
            (finding_id, status, reviewer_id, note.strip()[:NOTE_MAX], time.time()),
        )
        return told

    def history(self, finding_id: str) -> list[dict[str, Any]]:
        """The verdicts given on a finding, oldest first, with the reviewer's name and note (the finding's page shows them)."""
        return [
            {"status": r["status"], "by": r["name"] or "command line", "note": r["note"], "at": iso(r["at"])}
            for r in self.db._conn().execute(
                "SELECT l.*, u.name FROM acc_verdict_log l LEFT JOIN acc_users u ON u.id=l.user_id WHERE l.finding_id=? ORDER BY l.at",
                (finding_id,),
            )
        ]
