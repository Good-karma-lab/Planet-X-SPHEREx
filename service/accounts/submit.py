"""The submit that finishes by itself (decided with the user on 16 Nov 2026).

"Check and submit" is said once: the investigation is `declared` with its checks job and the user may leave. The checks are slow
(JPL takes minutes, the NEOWISE archive up to twenty), so nothing waits for a page:

  every check answered, none failed, a comment for every warning   → the finding is submitted here, the user is told
                                                                       "submitted, waiting for the verdict"
  a check failed (or gave no answer in the end)                     → the user is told which check and why; nothing is submitted
  warnings without comments                                         → the user is told how many comments are needed; the submit
                                                                       happens by itself when the notes are there

`settle` is called when the checks stream says a job reached its final state (`st.on_checks_done(body)`), when the user declares,
when a note is added, and whenever the user's data is read; every message is sent once.

The state of an investigation: `open` | `checking` | `needs_comment` | `blocked` | `submitted`.
"""

from __future__ import annotations

from typing import Any

from service.accounts.db import AccountsDB

WORD = {
    "two_passes": "two passes", "straight_line": "straight line", "gaia": "Gaia", "jpl": "JPL", "simbad": "SIMBAD",
    "edge_bright": "edge and bright stars", "above_bar": "above the bar", "neowise": "NEOWISE",
}  # fmt: skip


def checks_body(st: Any, job: str | None) -> dict[str, Any] | None:
    """What the checks stream says about a job: None when the stream is not loaded or the job is unknown."""
    look = getattr(st, "checks_status", None)
    if not job or not callable(look):
        return None
    body = look(job)
    return body if isinstance(body, dict) else None


def rows_of(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [c for c in (body or {}).get("checks") or [] if isinstance(c, dict)]


def progress_of(body: dict[str, Any] | None) -> str:
    """One line about the running checks: how many have answered, and what the slowest one says it is doing."""
    rows = rows_of(body)
    if not rows or (body or {}).get("state") == "done":
        return ""
    waiting = [c for c in rows if c.get("state") in ("running", "queued", "waiting")]
    said = next((str(c.get("detail") or "") for c in waiting if c.get("state") == "running" and c.get("detail")), "")
    return f"{len(rows) - len(waiting)} of {len(rows)} checks answered" + (f" · {said}" if said else "")


def state_of(inv: dict[str, Any], body: dict[str, Any] | None) -> str:
    if inv.get("submitted"):
        return "submitted"
    if not inv.get("declared"):
        return "open"
    if body is None or body.get("state") != "done":
        return "checking"
    if body.get("can_submit") is not True:
        return "blocked"
    warns = [c for c in rows_of(body) if c.get("state") == "warn"]
    return "needs_comment" if len(inv.get("notes") or []) < len(warns) else "ready"


def place_of(db: AccountsDB, user_id: str, inv: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    place = {k: body.get(k) for k in ("field_key", "ra", "dec")}
    if place["ra"] is None and inv["mark_ids"]:
        first = next((m for m in db.marks(user_id) if m["id"] in inv["mark_ids"]), None)
        if first is not None:
            place = {k: first[k] for k in ("field_key", "ra", "dec")}
    return place


def submit_now(st: Any, db: AccountsDB, user_id: str, inv: dict[str, Any], job: str, body: dict[str, Any]) -> dict[str, Any]:
    """Record the finding (the checks allow it): the credit, the notification, the investigation marked submitted, the points."""
    from service.accounts import score

    rows = [{k: c.get(k) for k in ("id", "state", "detail")} for c in rows_of(body)]
    sub = db.submit(user_id, inv, job, place_of(db, user_id, inv, body), rows)
    for mid in inv["mark_ids"][:1]:
        db.credit(sub["finding_id"], user_id, "marked", mid)
    sc = score.get(st, db)
    sc.submitted(user_id, sub["finding_id"], rows)
    sc.award(user_id)
    return sub


def settle(st: Any, db: AccountsDB, user_id: str, inv: dict[str, Any]) -> str:
    """Move a declared investigation as far as its checks allow; returns its state."""
    body = checks_body(st, inv.get("checks_job"))
    state = state_of(inv, body)
    if state in ("open", "checking", "submitted") or body is None:
        return state
    job = str(inv["checks_job"])
    ref = f"#/me?tab=investigations&inv={inv['id']}"
    rows = rows_of(body)
    if state == "blocked":
        bad = [c for c in rows if c.get("state") == "fail"] or [c for c in rows if c.get("state") not in ("pass", "warn")]
        why = "; ".join(f"{WORD.get(str(c.get('id')), str(c.get('id')))}: {c.get('detail') or c.get('state')}" for c in bad[:3])
        db.notify_once(user_id, f"blocked:{job}", "checks", f"“{inv['title']}” was not submitted. {why}"[:900], ref)
        return state
    if state == "needs_comment":
        warns = [c for c in rows if c.get("state") == "warn"]
        need = len(warns) - len(inv.get("notes") or [])
        names = ", ".join(WORD.get(str(c.get("id")), str(c.get("id"))) for c in warns)
        text = f"“{inv['title']}”: every check has answered. {'One comment is' if need == 1 else f'{need} comments are'} needed"
        db.notify_once(
            user_id, f"comment:{job}:{need}", "checks", f"{text} for the warnings ({names}); then it is submitted by itself.", ref
        )
        return state
    submit_now(st, db, user_id, inv, job, body)
    return "submitted"


def settle_job(st: Any, db: AccountsDB, job: str) -> int:
    """The checks stream's completion hook: every declared investigation that waits for this job."""
    n = 0
    for user_id, inv in db.declared_for_job(job):
        settle(st, db, user_id, inv)
        n += 1
    return n


def settle_user(st: Any, db: AccountsDB, user_id: str) -> None:
    for inv in db.investigations(user_id):
        if inv.get("declared") and not inv.get("submitted"):
            settle(st, db, user_id, inv)
