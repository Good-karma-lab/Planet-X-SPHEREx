"""The task kinds whose answers are judged (product spec §5b), next to "Look through a square" and "Check a machine finding":

  moves    "The machine says this moves. Does it?"   It moves · It stands still · It is an artefact · Can't tell
           judged like a checked finding: when the finding's stage is final. It moves ↔ known, candidate or confirmed (a known
           mover does move); It stands still and It is an artefact ↔ artefact (the stage does not tell the two apart).
  red      "Why is it red?"       Cold brown dwarf · Dusty or reddened star · Galaxy or extended · Artefact · Can't tell
  anomaly  "Is this feature real?"   Real feature · Detector edge or ghost · Cosmic ray or one bad exposure · Can't tell
           (before 17 Nov 2026: these one-click answers are still taken; the tasks now carry the question series of
           service/accounts/people.py, spec §5e)
           no machine verdict exists for these two, so they settle by agreement: a find is shown to people until AGREE_N answers
           agree or MAX_ANSWERS answers are given (Can't tell counts as given). The result is stored on the find
           ("people say: cold brown dwarf, 3 of 3"); with no single most common answer the result is "no agreement".

Points: POINTS["check"] (10) to an answer that agrees with the final stage or with the people's result; an answer that differs
counts as wrong in the accuracy; "Can't tell" is never wrong and never paid. One answer per user per find and kind; the first stays.
A red source that people call a cold brown dwarf goes to the queue of the brown-dwarf search (`acc_bd_queue`).
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from service.accounts.db import AccountsDB, iso
from service.accounts.score import FINAL_STAGES, POINTS, Score

AGREE_N = 3  # this many agreeing answers settle a red source or an anomaly
MAX_ANSWERS = 5  # … or this many answers in all
CANT_TELL = "cant_tell"
KINDS = ("moves", "red", "anomaly")
#: what a machine finding must be to be asked about, by task kind (service.findings kinds)
FIND_KINDS = {"moves": ("mover", "film", "browndwarf"), "red": ("red",), "anomaly": ("anomaly",)}
QUESTION = {
    "moves": "The machine says this moves. Does it?",
    "red": "Why is it red?",
    "anomaly": "Is this feature real?",
}
ANSWERS: dict[str, tuple[tuple[str, str], ...]] = {
    "moves": (("moves", "It moves"), ("still", "It stands still"), ("artefact", "It is an artefact"), (CANT_TELL, "Can't tell")),
    "red": (
        ("brown_dwarf", "Cold brown dwarf"),
        ("dusty_star", "Dusty or reddened star"),
        ("galaxy", "Galaxy or extended"),
        ("artefact", "Artefact"),
        (CANT_TELL, "Can't tell"),
    ),
    "anomaly": (
        ("real", "Real feature"),
        ("detector", "Detector edge or ghost"),
        ("cosmic_ray", "Cosmic ray or one bad exposure"),
        (CANT_TELL, "Can't tell"),
    ),
}
#: the final stages a "Does it really move?" answer agrees with
MOVES_AGREES = {"moves": ("known", "candidate", "confirmed"), "still": ("artefact",), "artefact": ("artefact",)}
BY_AGREEMENT = ("red", "anomaly")

SCHEMA = """
CREATE TABLE IF NOT EXISTS acc_task_answers (
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  answer TEXT NOT NULL,
  at REAL NOT NULL,
  settled REAL,
  PRIMARY KEY (user_id, kind, finding_id)
);
CREATE INDEX IF NOT EXISTS acc_task_answers_find ON acc_task_answers(kind, finding_id);
CREATE TABLE IF NOT EXISTS acc_people_say (
  kind TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  result TEXT,
  agree INTEGER NOT NULL,
  total INTEGER NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY (kind, finding_id)
);
CREATE TABLE IF NOT EXISTS acc_bd_queue (
  finding_id TEXT PRIMARY KEY,
  ra REAL,
  dec REAL,
  agree INTEGER NOT NULL,
  total INTEGER NOT NULL,
  at REAL NOT NULL
);
"""


def label_of(kind: str, answer: str | None) -> str:
    return next((label for a, label in ANSWERS.get(kind, ()) if a == answer), "no agreement" if answer is None else str(answer))


class Agreement:
    def __init__(self, score: Score) -> None:
        self.score = score
        self.db: AccountsDB = score.db
        self.db._conn().executescript(SCHEMA)

    # ------------------------------------------------------------------------------------------------------------ what to ask

    def counts(self, kind: str) -> dict[str, int]:
        """How many answers each find of this kind has."""
        rows = self.db._conn().execute("SELECT finding_id, COUNT(*) n FROM acc_task_answers WHERE kind=? GROUP BY finding_id", (kind,))
        return {r["finding_id"]: int(r["n"]) for r in rows}

    def settled_ids(self, kind: str) -> set[str]:
        return {r[0] for r in self.db._conn().execute("SELECT finding_id FROM acc_people_say WHERE kind=?", (kind,))}

    def answered_by(self, user_id: str, kind: str) -> set[str]:
        return {
            r[0] for r in self.db._conn().execute("SELECT finding_id FROM acc_task_answers WHERE user_id=? AND kind=?", (user_id, kind))
        }

    def pick(self, kind: str, rows: list[dict[str, Any]], user_id: str | None, seen: set[str]) -> dict[str, Any] | None:
        """The next find to ask about, from `rows` (the Findings page's order): one this user has no task or answer for.
        By agreement: a find people already answered comes first (it is the nearest to a result), settled ones are not asked.
        Does it move: finds without a final stage first, the least answered first."""
        mine = seen | (self.answered_by(user_id, kind) if user_id else set())
        n = self.counts(kind)
        pool = [r for r in rows if r.get("kind") in FIND_KINDS[kind] and r["finding_id"] not in mine]
        if kind in BY_AGREEMENT:
            done = self.settled_ids(kind)
            pool = [r for r in pool if r["finding_id"] not in done]
            pool.sort(key=lambda r: -n.get(r["finding_id"], 0))  # stable: the page's order among the equally answered
        else:
            pool.sort(key=lambda r: (r.get("stage") in FINAL_STAGES, n.get(r["finding_id"], 0)))
        return pool[0] if pool else None

    # ----------------------------------------------------------------------------------------------------------------- answers

    def answer(self, user_id: str, kind: str, finding_id: str, answer: str, place: dict[str, Any] | None = None) -> int:
        """Record the user's answer (the first one stays) and settle what can be settled. Returns the points paid to them now."""
        if kind not in KINDS or answer not in {a for a, _ in ANSWERS[kind]} or not finding_id:
            return 0
        c = self.db._conn()
        c.execute(
            "INSERT OR IGNORE INTO acc_task_answers(user_id,kind,finding_id,answer,at) VALUES(?,?,?,?,?)",
            (user_id, kind, finding_id, answer, time.time()),
        )
        before = self._paid(user_id)
        if kind in BY_AGREEMENT:
            self.settle_by_agreement(kind, finding_id, place or {})
        else:
            self.settle_moves(finding_id=finding_id)
        return self._paid(user_id) - before

    def _paid(self, user_id: str) -> int:
        r = self.db._conn().execute(
            "SELECT COALESCE(SUM(points),0) FROM acc_score_events WHERE user_id=? AND kind IN ('moves','red','anomaly')", (user_id,)
        )
        return int(r.fetchone()[0])

    def settle_moves(self, user_id: str | None = None, finding_id: str | None = None) -> None:
        """Judge the waiting "Does it really move?" answers whose finding now has a final stage."""
        c = self.db._conn()
        q = "SELECT * FROM acc_task_answers WHERE kind='moves' AND settled IS NULL"
        args: list[str] = []
        if user_id:
            q, args = q + " AND user_id=?", [*args, user_id]
        if finding_id:
            q, args = q + " AND finding_id=?", [*args, finding_id]
        stages: dict[str, str | None] = {}
        for r in c.execute(q, args).fetchall():
            fid = r["finding_id"]
            if fid not in stages:
                stages[fid] = self.score.stage_of(fid)
            stage = stages[fid]
            if stage is None:
                continue
            if r["answer"] != CANT_TELL:
                right = stage in MOVES_AGREES[r["answer"]]
                self.score._event(
                    r["user_id"],
                    "moves",
                    fid,
                    POINTS["check"] if right else 0,
                    right,
                    {"answer": r["answer"], "stage": stage, "answered_at": r["at"]},
                )
            c.execute(
                "UPDATE acc_task_answers SET settled=? WHERE user_id=? AND kind='moves' AND finding_id=?", (time.time(), r["user_id"], fid)
            )

    def settle_by_agreement(self, kind: str, finding_id: str, place: dict[str, Any]) -> dict[str, Any] | None:
        """Store the people's result once AGREE_N answers agree or MAX_ANSWERS are given, and pay those who agreed."""
        c = self.db._conn()
        if c.execute("SELECT 1 FROM acc_people_say WHERE kind=? AND finding_id=?", (kind, finding_id)).fetchone():
            # a late answer on a settled find (the task was taken before it settled) is judged against the stored result
            self._judge(kind, finding_id)
            return self.people_say([finding_id], kind).get(finding_id)
        rows = c.execute(
            "SELECT user_id, answer FROM acc_task_answers WHERE kind=? AND finding_id=? ORDER BY at", (kind, finding_id)
        ).fetchall()
        real = Counter(r["answer"] for r in rows if r["answer"] != CANT_TELL)
        top = real.most_common(2)
        agreed = bool(top) and top[0][1] >= AGREE_N
        if not agreed and len(rows) < MAX_ANSWERS:
            return None
        clear = bool(top) and (len(top) == 1 or top[0][1] > top[1][1])  # a single most common answer
        result = top[0][0] if clear else None
        c.execute(
            "INSERT OR IGNORE INTO acc_people_say(kind,finding_id,result,agree,total,at) VALUES(?,?,?,?,?,?)",
            (kind, finding_id, result, top[0][1] if clear else 0, len(rows), time.time()),
        )
        if kind == "red" and result == "brown_dwarf":
            c.execute(
                "INSERT OR IGNORE INTO acc_bd_queue(finding_id,ra,dec,agree,total,at) VALUES(?,?,?,?,?,?)",
                (finding_id, place.get("ra"), place.get("dec"), top[0][1], len(rows), time.time()),
            )
        self._judge(kind, finding_id)
        return self.people_say([finding_id], kind).get(finding_id)

    def _judge(self, kind: str, finding_id: str) -> None:
        c = self.db._conn()
        said = c.execute("SELECT result FROM acc_people_say WHERE kind=? AND finding_id=?", (kind, finding_id)).fetchone()
        if said is None:
            return
        for r in c.execute(
            "SELECT * FROM acc_task_answers WHERE kind=? AND finding_id=? AND settled IS NULL", (kind, finding_id)
        ).fetchall():
            if r["answer"] != CANT_TELL and said["result"] is not None:  # with no agreement nobody is right or wrong
                right = r["answer"] == said["result"]
                self.score._event(
                    r["user_id"],
                    kind,
                    finding_id,
                    POINTS["check"] if right else 0,
                    right,
                    {"answer": r["answer"], "result": said["result"], "answered_at": r["at"]},
                )
            c.execute(
                "UPDATE acc_task_answers SET settled=? WHERE user_id=? AND kind=? AND finding_id=?",
                (time.time(), r["user_id"], kind, finding_id),
            )
            self.score.award(r["user_id"])

    # -------------------------------------------------------------------------------------------------------------- the results

    def people_say(self, finding_ids: list[str] | None = None, kind: str | None = None) -> dict[str, dict[str, Any]]:
        """{find id: {kind, result, label, agree, total, text}}: what people said about a find ("people say: cold brown dwarf, 3 of 3")."""
        q = "SELECT * FROM acc_people_say"
        args: list[str] = []
        cond: list[str] = []
        if kind:
            cond, args = [*cond, "kind=?"], [*args, kind]
        if finding_ids is not None:
            if not finding_ids:
                return {}
            cond, args = [*cond, f"finding_id IN ({','.join('?' * len(finding_ids))})"], [*args, *finding_ids]
        if cond:
            q += " WHERE " + " AND ".join(cond)
        out = {}
        for r in self.db._conn().execute(q, args):
            label = label_of(r["kind"], r["result"])
            text = (
                f"people say: {label.lower()}, {r['agree']} of {r['total']}"
                if r["result"]
                else f"people do not agree ({r['total']} answers)"
            )
            out[r["finding_id"]] = {
                "kind": r["kind"],
                "result": r["result"],
                "label": label,
                "agree": r["agree"],
                "total": r["total"],
                "text": text,
                "at": iso(r["at"]),
            }
        return out

    def brown_dwarf_queue(self) -> list[dict[str, Any]]:
        """The red sources people call cold brown dwarfs, the oldest first: what the brown-dwarf search should look at."""
        rows = self.db._conn().execute("SELECT * FROM acc_bd_queue ORDER BY at")
        return [
            {"finding_id": r["finding_id"], "ra": r["ra"], "dec": r["dec"], "agree": r["agree"], "total": r["total"], "at": iso(r["at"])}
            for r in rows
        ]


def get(st: Any, db: AccountsDB) -> Agreement:
    from service.accounts import score

    a: Agreement | None = getattr(st, "agreement", None)
    if a is None or a.db is not db:
        a = st.agreement = Agreement(score.get(st, db))
    return a


__all__ = ["AGREE_N", "ANSWERS", "KINDS", "MAX_ANSWERS", "QUESTION", "Agreement", "get"]
