# ruff: noqa: E501
"""The accounts part of the product database (`SKY_PRODUCT_DB`): users, sign-in tokens, sessions, marks, tasks, investigations,
view history, notifications, submitted findings and credits. No password is stored anywhere; tokens are stored only as hashes."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS acc_users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  google_sub TEXT,
  created REAL NOT NULL,
  last_seen REAL
);
CREATE TABLE IF NOT EXISTS acc_login_tokens (
  hash TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  next TEXT,
  expires REAL NOT NULL,
  used REAL
);
CREATE TABLE IF NOT EXISTS acc_sessions (
  hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created REAL NOT NULL,
  expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acc_sessions_user ON acc_sessions(user_id);
CREATE TABLE IF NOT EXISTS acc_marks (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  field_key TEXT NOT NULL,
  cell_id TEXT,
  ra REAL NOT NULL,
  dec REAL NOT NULL,
  pass INTEGER,
  label TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acc_marks_user ON acc_marks(user_id);
CREATE TABLE IF NOT EXISTS acc_tasks (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  kind TEXT NOT NULL,
  cell_id TEXT,
  finding_id TEXT,
  field_key TEXT,
  ra REAL,
  dec REAL,
  state TEXT NOT NULL DEFAULT 'open',
  result TEXT,
  mark_ids TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL,
  done_at REAL
);
CREATE INDEX IF NOT EXISTS acc_tasks_user ON acc_tasks(user_id);
CREATE TABLE IF NOT EXISTS acc_investigations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  finding_id TEXT,
  mark_ids TEXT NOT NULL DEFAULT '[]',
  notes TEXT NOT NULL DEFAULT '[]',
  frames TEXT NOT NULL DEFAULT '[]',
  checks_job TEXT,
  submitted TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acc_investigations_user ON acc_investigations(user_id);
CREATE TABLE IF NOT EXISTS acc_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  key TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acc_history_user ON acc_history(user_id, at);
CREATE TABLE IF NOT EXISTS acc_notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  ref TEXT,
  created REAL NOT NULL,
  read REAL
);
CREATE INDEX IF NOT EXISTS acc_notifications_user ON acc_notifications(user_id, created);
CREATE TABLE IF NOT EXISTS acc_submissions (
  finding_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  investigation_id TEXT NOT NULL UNIQUE,
  checks_job TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  field_key TEXT,
  ra REAL,
  dec REAL,
  status TEXT NOT NULL DEFAULT 'submitted',
  checks TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS acc_credits (
  finding_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL,
  result TEXT,
  at REAL NOT NULL,
  PRIMARY KEY (finding_id, user_id, role)
);
"""

MAX_MARKS = 5000
MAX_HISTORY = 500
MAX_INVESTIGATIONS = 500
#: the statuses of a finding (docs: product spec §3)
STATUSES = ("new", "in_review", "submitted", "known", "artefact", "candidate", "confirmed")


def product_db_path(store_dir: Path) -> Path:
    """`SKY_PRODUCT_DB`, else `<store>/product.sqlite` (the same rule as service.storage.product_db_path of the grid stream)."""
    env = os.environ.get("SKY_PRODUCT_DB")
    return Path(env).resolve() if env else Path(store_dir) / "product.sqlite"


def iso(t: float | None) -> str | None:
    return None if t is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class LimitReachedError(Exception):
    """The user already keeps the largest allowed number of these."""


class AccountsDB:
    """One connection per thread (WAL); other product modules keep their own tables in the same file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conn().executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c: sqlite3.Connection | None = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.c = c
        return c

    # ------------------------------------------------------------------------------------------------------------- users, sign-in

    @staticmethod
    def _user(r: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if r is None else {"id": r["id"], "name": r["name"], "email": r["email"]}

    def user(self, user_id: str) -> dict[str, Any] | None:
        return self._user(self._conn().execute("SELECT * FROM acc_users WHERE id=?", (user_id,)).fetchone())

    def user_for_email(self, email: str, name: str | None = None, google_sub: str | None = None) -> dict[str, Any]:
        """The user of this address, created at the first sign-in."""
        c = self._conn()
        now = time.time()
        c.execute(
            "INSERT OR IGNORE INTO acc_users(id,email,name,google_sub,created) VALUES(?,?,?,?,?)",
            (new_id("u"), email, (name or email.split("@")[0])[:60], google_sub, now),
        )
        if google_sub:
            c.execute("UPDATE acc_users SET google_sub=? WHERE email=? AND google_sub IS NULL", (google_sub, email))
        u = self._user(c.execute("SELECT * FROM acc_users WHERE email=?", (email,)).fetchone())
        assert u is not None
        return u

    def rename(self, user_id: str, name: str) -> None:
        self._conn().execute("UPDATE acc_users SET name=? WHERE id=?", (name, user_id))

    def new_login_token(self, email: str, ttl_s: float, nxt: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        c = self._conn()
        now = time.time()
        c.execute("DELETE FROM acc_login_tokens WHERE expires < ?", (now - 86400,))
        c.execute("INSERT INTO acc_login_tokens(hash,email,next,expires) VALUES(?,?,?,?)", (token_hash(token), email, nxt, now + ttl_s))
        return token

    def use_login_token(self, token: str) -> tuple[str, str | None] | None:
        """(email, next) once; None for an unknown, a used or an expired token."""
        c = self._conn()
        now = time.time()
        cur = c.execute("UPDATE acc_login_tokens SET used=? WHERE hash=? AND used IS NULL AND expires>=?", (now, token_hash(token), now))
        if cur.rowcount != 1:
            return None
        r = c.execute("SELECT email, next FROM acc_login_tokens WHERE hash=?", (token_hash(token),)).fetchone()
        return (r["email"], r["next"])

    def new_session(self, user_id: str, ttl_s: float) -> str:
        token = secrets.token_urlsafe(32)
        c = self._conn()
        now = time.time()
        c.execute("DELETE FROM acc_sessions WHERE expires < ?", (now,))
        c.execute("INSERT INTO acc_sessions(hash,user_id,created,expires) VALUES(?,?,?,?)", (token_hash(token), user_id, now, now + ttl_s))
        c.execute("UPDATE acc_users SET last_seen=? WHERE id=?", (now, user_id))
        return token

    def session_user(self, token: str) -> dict[str, Any] | None:
        r = (
            self._conn()
            .execute(
                "SELECT u.* FROM acc_sessions s JOIN acc_users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>=?",
                (token_hash(token), time.time()),
            )
            .fetchone()
        )
        return self._user(r)

    def end_session(self, token: str) -> None:
        self._conn().execute("DELETE FROM acc_sessions WHERE hash=?", (token_hash(token),))

    # ---------------------------------------------------------------------------------------------------------------------- marks

    @staticmethod
    def _mark(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"], "field_key": r["field_key"], "cell_id": r["cell_id"], "ra": r["ra"], "dec": r["dec"], "pass": r["pass"],
            "label": r["label"], "level": r["level"], "note": r["note"], "created": r["created"],
        }  # fmt: skip

    def marks(self, user_id: str, field_key: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM acc_marks WHERE user_id=?" + (" AND field_key=?" if field_key else "") + " ORDER BY created DESC, id"
        return [self._mark(r) for r in self._conn().execute(q, (user_id, field_key) if field_key else (user_id,))]

    def put_mark(self, user_id: str, m: dict[str, Any]) -> dict[str, Any] | None:
        """Insert, or replace the user's own mark of this id. None when the id belongs to somebody else."""
        c = self._conn()
        have = c.execute("SELECT user_id FROM acc_marks WHERE id=?", (m["id"],)).fetchone()
        if have is not None and have["user_id"] != user_id:
            return None
        if have is None and c.execute("SELECT COUNT(*) FROM acc_marks WHERE user_id=?", (user_id,)).fetchone()[0] >= MAX_MARKS:
            raise LimitReachedError
        c.execute(
            "INSERT INTO acc_marks(id,user_id,field_key,cell_id,ra,dec,pass,label,level,note,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET field_key=excluded.field_key, cell_id=excluded.cell_id, ra=excluded.ra, dec=excluded.dec,"
            " pass=excluded.pass, label=excluded.label, level=excluded.level, note=excluded.note, updated=excluded.updated",
            (m["id"], user_id, m["field_key"], m["cell_id"], m["ra"], m["dec"], m["pass"], m["label"], m["level"], m["note"], m["created"],
             time.time()),
        )  # fmt: skip
        return self._mark(c.execute("SELECT * FROM acc_marks WHERE id=?", (m["id"],)).fetchone())

    def delete_mark(self, user_id: str, mark_id: str) -> bool:
        return self._conn().execute("DELETE FROM acc_marks WHERE id=? AND user_id=?", (mark_id, user_id)).rowcount == 1

    # ---------------------------------------------------------------------------------------------------------------------- tasks

    @staticmethod
    def _task(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"], "kind": r["kind"], "cell_id": r["cell_id"], "finding_id": r["finding_id"], "field_key": r["field_key"],
            "ra": r["ra"], "dec": r["dec"], "state": r["state"], "result": r["result"], "mark_ids": json.loads(r["mark_ids"]),
            "created": iso(r["created"]), "done_at": iso(r["done_at"]),
        }  # fmt: skip

    def tasks(self, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn().execute("SELECT * FROM acc_tasks WHERE user_id=? ORDER BY state DESC, created DESC", (user_id,))
        return [self._task(r) for r in rows]

    def task(self, task_id: str) -> tuple[dict[str, Any], str | None] | None:
        r = self._conn().execute("SELECT * FROM acc_tasks WHERE id=?", (task_id,)).fetchone()
        return None if r is None else (self._task(r), r["user_id"])

    def open_task(self, user_id: str, kind: str) -> dict[str, Any] | None:
        r = (
            self._conn()
            .execute("SELECT * FROM acc_tasks WHERE user_id=? AND kind=? AND state='open' ORDER BY created DESC LIMIT 1", (user_id, kind))
            .fetchone()
        )
        return None if r is None else self._task(r)

    def task_refs(self, user_id: str, kind: str) -> set[str]:
        """The squares (or findings) this user already has a task for."""
        col = "cell_id" if kind == "cell" else "finding_id"
        return {r[0] for r in self._conn().execute(f"SELECT {col} FROM acc_tasks WHERE user_id=? AND kind=?", (user_id, kind)) if r[0]}

    def add_task(self, user_id: str | None, kind: str, ref: dict[str, Any]) -> dict[str, Any]:
        """A new open task; the user's own open task for the same square or finding is returned instead of a second one."""
        c = self._conn()
        col = "cell_id" if kind == "cell" else "finding_id"
        if user_id is not None:
            r = c.execute(
                f"SELECT * FROM acc_tasks WHERE user_id=? AND kind=? AND {col}=? AND state='open'", (user_id, kind, ref[col])
            ).fetchone()
            if r is not None:
                return self._task(r)
        tid = new_id("t")
        c.execute("DELETE FROM acc_tasks WHERE user_id IS NULL AND created < ?", (time.time() - 7 * 86400,))  # guests' tasks are not kept
        c.execute(
            "INSERT INTO acc_tasks(id,user_id,kind,cell_id,finding_id,field_key,ra,dec,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                tid,
                user_id,
                kind,
                ref.get("cell_id"),
                ref.get("finding_id"),
                ref.get("field_key"),
                ref.get("ra"),
                ref.get("dec"),
                time.time(),
            ),
        )
        return self._task(c.execute("SELECT * FROM acc_tasks WHERE id=?", (tid,)).fetchone())

    def finish_task(self, task_id: str, result: str, mark_ids: list[str]) -> dict[str, Any]:
        c = self._conn()
        c.execute(
            "UPDATE acc_tasks SET state='done', result=?, mark_ids=?, done_at=? WHERE id=?",
            (result, json.dumps(mark_ids), time.time(), task_id),
        )
        return self._task(c.execute("SELECT * FROM acc_tasks WHERE id=?", (task_id,)).fetchone())

    def claim_task(self, task_id: str, user_id: str) -> None:
        """A task taken as a guest becomes the user's when they sign in and close it."""
        self._conn().execute("UPDATE acc_tasks SET user_id=? WHERE id=? AND user_id IS NULL", (user_id, task_id))

    # ------------------------------------------------------------------------------------------------------------- investigations

    @staticmethod
    def _inv(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"], "title": r["title"], "finding_id": r["finding_id"], "mark_ids": json.loads(r["mark_ids"]),
            "notes": json.loads(r["notes"]), "frames": json.loads(r["frames"]), "checks_job": r["checks_job"],
            "submitted": r["submitted"], "created": iso(r["created"]), "updated": iso(r["updated"]),
        }  # fmt: skip

    def investigations(self, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn().execute("SELECT * FROM acc_investigations WHERE user_id=? ORDER BY updated DESC", (user_id,))
        return [self._inv(r) for r in rows]

    def investigation(self, user_id: str, inv_id: str) -> dict[str, Any] | None:
        r = self._conn().execute("SELECT * FROM acc_investigations WHERE id=? AND user_id=?", (inv_id, user_id)).fetchone()
        return None if r is None else self._inv(r)

    def add_investigation(self, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        c = self._conn()
        if c.execute("SELECT COUNT(*) FROM acc_investigations WHERE user_id=?", (user_id,)).fetchone()[0] >= MAX_INVESTIGATIONS:
            raise LimitReachedError
        iid = new_id("i")
        now = time.time()
        c.execute(
            "INSERT INTO acc_investigations(id,user_id,title,finding_id,mark_ids,notes,frames,checks_job,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (iid, user_id, body["title"], body.get("finding_id"), json.dumps(body.get("mark_ids") or []), json.dumps(body.get("notes") or []),
             json.dumps(body.get("frames") or []), body.get("checks_job"), now, now),
        )  # fmt: skip
        inv = self.investigation(user_id, iid)
        assert inv is not None
        return inv

    def patch_investigation(self, user_id: str, inv_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        cols = {"title": str, "finding_id": str, "checks_job": str, "mark_ids": json.dumps, "notes": json.dumps, "frames": json.dumps}
        sets = [(k, v if v is None or cols[k] is str else json.dumps(v)) for k, v in changes.items() if k in cols]
        if sets:
            q = "UPDATE acc_investigations SET " + ", ".join(f"{k}=?" for k, _ in sets) + ", updated=? WHERE id=? AND user_id=?"
            self._conn().execute(q, (*[v for _, v in sets], time.time(), inv_id, user_id))
        return self.investigation(user_id, inv_id)

    def delete_investigation(self, user_id: str, inv_id: str) -> bool:
        return self._conn().execute("DELETE FROM acc_investigations WHERE id=? AND user_id=?", (inv_id, user_id)).rowcount == 1

    # -------------------------------------------------------------------------------------------------------------------- history

    def history(self, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn().execute("SELECT * FROM acc_history WHERE user_id=? ORDER BY at DESC, id DESC LIMIT ?", (user_id, limit))
        return [{"id": r["id"], "kind": r["kind"], "ref": r["ref"], "title": r["title"], "at": iso(r["at"])} for r in rows]

    def add_history(self, user_id: str, kind: str, ref: str, title: str, key: str | None = None) -> None:
        """The same place opened again moves to the top instead of filling the list. `key` names the place when the link to it
        changes while the user works there (the lab's address carries the display state); without it the link is the place."""
        c = self._conn()
        now = time.time()
        key = key or ref
        cur = c.execute(
            "UPDATE acc_history SET at=?, title=?, kind=?, ref=? WHERE user_id=? AND key=?", (now, title, kind, ref, user_id, key)
        )
        if cur.rowcount == 0:
            c.execute("INSERT INTO acc_history(user_id,kind,ref,title,key,at) VALUES(?,?,?,?,?,?)", (user_id, kind, ref, title, key, now))
            c.execute(
                "DELETE FROM acc_history WHERE user_id=? AND id NOT IN (SELECT id FROM acc_history WHERE user_id=? ORDER BY at DESC LIMIT ?)",
                (user_id, user_id, MAX_HISTORY),
            )

    def clear_history(self, user_id: str) -> None:
        self._conn().execute("DELETE FROM acc_history WHERE user_id=?", (user_id,))

    # -------------------------------------------------------------------------------------------------------------- notifications

    def notifications(self, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT * FROM acc_notifications WHERE user_id=? ORDER BY created DESC, id DESC LIMIT ?", (user_id, limit)
        )
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "text": r["text"],
                "ref": r["ref"],
                "created": iso(r["created"]),
                "read": r["read"] is not None,
            }
            for r in rows
        ]

    def notify(self, user_id: str, kind: str, text: str, ref: str | None = None) -> None:
        self._conn().execute(
            "INSERT INTO acc_notifications(user_id,kind,text,ref,created) VALUES(?,?,?,?,?)", (user_id, kind, text, ref, time.time())
        )

    def read_notifications(self, user_id: str, ids: list[int] | None) -> None:
        c = self._conn()
        if ids is None:
            c.execute("UPDATE acc_notifications SET read=? WHERE user_id=? AND read IS NULL", (time.time(), user_id))
        else:
            c.executemany(
                "UPDATE acc_notifications SET read=? WHERE user_id=? AND id=? AND read IS NULL", [(time.time(), user_id, i) for i in ids]
            )

    # ------------------------------------------------------------------------------------------------- submitted findings, credit

    def _sub(self, r: sqlite3.Row) -> dict[str, Any]:
        return {
            "finding_id": r["finding_id"], "investigation_id": r["investigation_id"], "checks_job": r["checks_job"], "title": r["title"],
            "field_key": r["field_key"], "ra": r["ra"], "dec": r["dec"], "status": r["status"], "checks": json.loads(r["checks"]),
            "created": iso(r["created"]), "updated": iso(r["updated"]),
        }  # fmt: skip

    def submission_of(self, investigation_id: str) -> dict[str, Any] | None:
        r = self._conn().execute("SELECT * FROM acc_submissions WHERE investigation_id=?", (investigation_id,)).fetchone()
        return None if r is None else self._sub(r)

    def submit(
        self, user_id: str, inv: dict[str, Any], checks_job: str, place: dict[str, Any], checks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Record the finding under the user's name (once per investigation) and tell the user."""
        have = self.submission_of(inv["id"])
        if have is not None:
            return have
        c = self._conn()
        fid = inv["finding_id"] or f"user:{secrets.token_hex(6)}"
        now = time.time()
        c.execute(
            "INSERT INTO acc_submissions(finding_id,user_id,investigation_id,checks_job,title,field_key,ra,dec,checks,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (fid, user_id, inv["id"], checks_job, inv["title"], place.get("field_key"), place.get("ra"), place.get("dec"), json.dumps(checks),
             now, now),
        )  # fmt: skip
        c.execute("UPDATE acc_investigations SET submitted=?, checks_job=?, updated=? WHERE id=?", (fid, checks_job, now, inv["id"]))
        self.credit(fid, user_id, "submitted")
        self.notify(user_id, "submitted", f"Your finding “{inv['title']}” is submitted and waits for the verdict.", fid)
        sub = self.submission_of(inv["id"])
        assert sub is not None
        return sub

    def credit(self, finding_id: str, user_id: str, role: str, result: str | None = None) -> None:
        """role: `marked` (first to mark it), `checked` (closed its task), `submitted`. The first record of a role by a user stays."""
        self._conn().execute(
            "INSERT INTO acc_credits(finding_id,user_id,role,result,at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(finding_id,user_id,role) DO UPDATE SET result=excluded.result",
            (finding_id, user_id, role, result, time.time()),
        )

    def credits_of(self, finding_ids: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """{finding id: [{name, role, result, at}]} oldest first: the public credit line (names only, never an address)."""
        q = "SELECT c.finding_id, c.role, c.result, c.at, u.name FROM acc_credits c JOIN acc_users u ON u.id=c.user_id"
        args: tuple[Any, ...] = ()
        if finding_ids is not None:
            q += f" WHERE c.finding_id IN ({','.join('?' * len(finding_ids))})"
            args = tuple(finding_ids)
        out: dict[str, list[dict[str, Any]]] = {}
        for r in self._conn().execute(q + " ORDER BY c.at", args):
            out.setdefault(r["finding_id"], []).append({"name": r["name"], "role": r["role"], "result": r["result"], "at": iso(r["at"])})
        return out

    def check_counts(self) -> dict[str, int]:
        """How many people closed a task on each finding."""
        rows = self._conn().execute("SELECT finding_id, COUNT(*) n FROM acc_credits WHERE role='checked' GROUP BY finding_id")
        return {r["finding_id"]: int(r["n"]) for r in rows}

    def submitted(self) -> list[dict[str, Any]]:
        """Every finding submitted by a person, newest first, with its credit line."""
        rows = [self._sub(r) for r in self._conn().execute("SELECT * FROM acc_submissions ORDER BY created DESC")]
        cr = self.credits_of([s["finding_id"] for s in rows]) if rows else {}
        return [{**s, "credit": cr.get(s["finding_id"], [])} for s in rows]

    def my_findings(self, user_id: str) -> list[dict[str, Any]]:
        """What the user submitted, and the machine findings they checked."""
        c = self._conn()
        out = [{**self._sub(r), "role": "submitted", "result": None} for r in c.execute(
            "SELECT * FROM acc_submissions WHERE user_id=? ORDER BY created DESC", (user_id,))]  # fmt: skip
        mine = {s["finding_id"] for s in out}
        status = {r["finding_id"]: r["status"] for r in c.execute("SELECT finding_id, status FROM acc_submissions")}
        for r in c.execute("SELECT * FROM acc_credits WHERE user_id=? AND role!='submitted' ORDER BY at DESC", (user_id,)):
            if r["finding_id"] not in mine:
                out.append({"finding_id": r["finding_id"], "role": r["role"], "result": r["result"], "created": iso(r["at"]),
                            "status": status.get(r["finding_id"], "in_review"), "title": "", "ra": None, "dec": None, "field_key": None})  # fmt: skip
        return out

    def set_status(self, finding_id: str, status: str) -> int:
        """The verdict on a finding: everyone credited on it gets a notification. Returns how many people were told."""
        if status not in STATUSES:
            raise ValueError(f"status is one of {', '.join(STATUSES)}")
        c = self._conn()
        c.execute("UPDATE acc_submissions SET status=?, updated=? WHERE finding_id=?", (status, time.time(), finding_id))
        users = {r[0] for r in c.execute("SELECT DISTINCT user_id FROM acc_credits WHERE finding_id=?", (finding_id,))}
        for u in users:
            self.notify(u, "status", f"Finding {finding_id}: the status is now “{status.replace('_', ' ')}”.", finding_id)
        return len(users)
