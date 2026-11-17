# ruff: noqa: E501
"""Sharing (product spec §5c): public pages a person may send to others, each with a picture made for it.

  profile      `#/u/<slug>`                 public name, points, level, badges, findings. Public only when its owner turned it on
                                            (sharing turns it on, with the click that shares)
  achievement  `#/u/<slug>?badge=<id>`      the profile with one badge in front
  story        `#/findings/<id>/story`      an article made from the investigation of a submitted finding; its owner reads and
                                            edits the text, and it is public only after they publish it
  discovery    the story of a confirmed finding: the finder, the checkers, a press-style summary

A shared link is `/s/<kind>/<ref>`: a small HTML page with the Open Graph tags (social crawlers do not run the app) that sends
people on to the app. Its picture is `/s/<kind>/<ref>/image.png`, 1200 × 630, drawn here. Counts: shares and visits per link and
channel only (`acc_share_counts`): no user, no address, no cookie, no third-party tracker. Nothing is posted by the service:
the page opens the network's own share window and the person posts there.
An e-mail is never part of a public answer.
"""

from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from typing import Any

from service.accounts import score as scoring
from service.accounts.db import AccountsDB, iso

CHANNELS = ("native", "x", "linkedin", "facebook", "reddit", "telegram", "whatsapp", "copy")
KINDS = ("profile", "achievement", "story", "discovery")
SITE = "SPHEREx Space"
OG_SIZE = (1200, 630)
BADGES = Path(__file__).resolve().parent.parent.parent / "web" / "public" / "badges"
CRAWLER = re.compile(r"bot|crawler|spider|facebookexternalhit|slackbot|whatsapp|telegram|linkedin|twitter|discord|preview", re.I)
FINAL_TEXT = {
    "submitted": "It passed every check and now waits for the verdict.",
    "in_review": "It is in review.",
    "known": "The verdict: a known object.",
    "artefact": "The verdict: an artefact.",
    "candidate": "The verdict: a candidate. It is in no catalogue we checked.",
    "confirmed": "Confirmed as a new object.",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS acc_stories (
  finding_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  intro TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  public INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  published REAL
);
CREATE TABLE IF NOT EXISTS acc_share_counts (
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  channel TEXT NOT NULL,
  shares INTEGER NOT NULL DEFAULT 0,
  visits INTEGER NOT NULL DEFAULT 0,
  previews INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (kind, ref, channel)
);
"""


def slug_of(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "searcher"


class Share:
    def __init__(self, st: Any, db: AccountsDB) -> None:
        self.st = st
        self.db = db
        self.score = scoring.get(st, db)
        c = db._conn()
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(acc_users)")}
        if "slug" not in cols:
            c.execute("ALTER TABLE acc_users ADD COLUMN slug TEXT")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS acc_users_slug ON acc_users(slug)")
        if "public_profile" not in cols:
            c.execute("ALTER TABLE acc_users ADD COLUMN public_profile INTEGER NOT NULL DEFAULT 0")

    # ---------------------------------------------------------------------------------------------------------------- profiles

    def slug(self, user_id: str) -> str:
        """The user's address on the site: made from the public name, unique (a second "ann" is "ann-2"). It follows a rename."""
        c = self.db._conn()
        r = c.execute("SELECT name, slug FROM acc_users WHERE id=?", (user_id,)).fetchone()
        if r is None:
            return ""
        want = slug_of(r["name"])
        if r["slug"] and (r["slug"] == want or re.fullmatch(re.escape(want) + r"-\d+", r["slug"])):
            return str(r["slug"])
        s, n = want, 1
        while c.execute("SELECT 1 FROM acc_users WHERE slug=? AND id!=?", (s, user_id)).fetchone():
            n += 1
            s = f"{want}-{n}"
        c.execute("UPDATE acc_users SET slug=? WHERE id=?", (s, user_id))
        return s

    def set_public(self, user_id: str, public: bool) -> str:
        self.db._conn().execute("UPDATE acc_users SET public_profile=? WHERE id=?", (int(public), user_id))
        return self.slug(user_id)

    def is_public(self, user_id: str) -> bool:
        r = self.db._conn().execute("SELECT public_profile FROM acc_users WHERE id=?", (user_id,)).fetchone()
        return bool(r and r["public_profile"])

    def user_of_slug(self, slug: str) -> str | None:
        r = self.db._conn().execute("SELECT id FROM acc_users WHERE slug=?", (slug,)).fetchone()
        return None if r is None else str(r["id"])

    def profile(self, slug: str, viewer: str | None = None) -> dict[str, Any] | None:
        """The public profile, or None when there is none or its owner keeps it private (the owner always sees their own)."""
        uid = self.user_of_slug(slug)
        if uid is None or not (self.is_public(uid) or uid == viewer):
            return None
        u = self.db.user(uid)
        assert u is not None
        s = self.score.summary(uid)
        c = self.db._conn()
        since = c.execute("SELECT created FROM acc_users WHERE id=?", (uid,)).fetchone()["created"]
        finds = [
            {"finding_id": r["finding_id"], "title": r["title"], "status": r["status"], "created": iso(r["created"]),
             "story": bool(c.execute("SELECT 1 FROM acc_stories WHERE finding_id=? AND public=1", (r["finding_id"],)).fetchone())}
            for r in c.execute("SELECT * FROM acc_submissions WHERE user_id=? ORDER BY created DESC", (uid,))
        ]  # fmt: skip
        return {
            "slug": slug, "name": u["name"], "points": s["points"], "level": s["level"], "accuracy": s["accuracy"], "answered": s["answered"],
            "squares": s["squares"], "since": iso(since), "public": self.is_public(uid), "mine": uid == viewer,
            "badges": [{k: a[k] for k in ("id", "family", "tier", "name", "line", "earned_at")} for a in s["achievements"] if a["earned_at"]],
            "findings": finds,
        }  # fmt: skip

    # ----------------------------------------------------------------------------------------------------------------- stories

    def _submission(self, finding_id: str) -> Any:
        return self.db._conn().execute("SELECT * FROM acc_submissions WHERE finding_id=?", (finding_id,)).fetchone()

    def _draft(self, sub: Any) -> dict[str, str]:
        """The first text of a story, made from the investigation: the user reads it and changes it before it is public."""
        c = self.db._conn()
        inv = c.execute("SELECT * FROM acc_investigations WHERE id=?", (sub["investigation_id"],)).fetchone()
        notes = json.loads(inv["notes"]) if inv is not None else []
        marks = len(json.loads(inv["mark_ids"])) if inv is not None else 0
        seen = (
            f"I marked it on {marks} passes of the SPHEREx survey"
            if marks >= 2
            else "The machine measured it in the visits of one SPHEREx pass"
            if sub["finding_id"].split(":")[0] in ("film", "mover")
            else "I marked it in the SPHEREx survey"
        )
        intro = (
            f"I found a point of light that moves in NASA's SPHEREx infrared survey. {seen}, "
            "and before it was submitted the service checked it against Gaia, SIMBAD, JPL's list of Solar System bodies and the NEOWISE archive."
        )
        return {
            "title": sub["title"] or "A moving object in the SPHEREx sky",
            "intro": intro,
            "notes": "\n\n".join(n["text"] for n in notes),
        }

    def story(self, finding_id: str, viewer: str | None = None, make: bool = False) -> dict[str, Any] | None:
        """The story of a submitted finding. Its owner always gets it (a draft is made at the first ask, with `make`); anyone
        else only once it is public."""
        c = self.db._conn()
        sub = self._submission(finding_id)
        if sub is None:
            return None
        mine = bool(viewer) and sub["user_id"] == viewer
        row = c.execute("SELECT * FROM acc_stories WHERE finding_id=?", (finding_id,)).fetchone()
        if row is None and mine and make:
            d = self._draft(sub)
            now = time.time()
            c.execute(
                "INSERT INTO acc_stories(finding_id,user_id,title,intro,notes,created,updated) VALUES(?,?,?,?,?,?,?)",
                (finding_id, sub["user_id"], d["title"], d["intro"], d["notes"], now, now),
            )
            row = c.execute("SELECT * FROM acc_stories WHERE finding_id=?", (finding_id,)).fetchone()
        if row is None or not (row["public"] or mine):
            return None
        finder = self.db.user(sub["user_id"]) if sub["user_id"] else None
        credits = self.db.credits_of([finding_id]).get(finding_id, [])
        checkers = list(dict.fromkeys(x["name"] for x in credits if x["role"] == "checked"))
        inv = c.execute("SELECT mark_ids FROM acc_investigations WHERE id=?", (sub["investigation_id"],)).fetchone()
        ids = json.loads(inv["mark_ids"]) if inv is not None else []
        marks = [
            {"ra": m["ra"], "dec": m["dec"], "pass": m["pass"], "label": m["label"]}
            for m in (self.db.marks(sub["user_id"]) if sub["user_id"] else [])
            if m["id"] in ids
        ]
        status = sub["status"]
        name = finder["name"] if finder else "a searcher"
        return {
            "finding_id": finding_id, "title": row["title"], "intro": row["intro"], "notes": row["notes"],
            "public": bool(row["public"]), "mine": mine, "published": iso(row["published"]), "updated": iso(row["updated"]),
            "status": status, "status_text": FINAL_TEXT.get(status, status), "discovery": status == "confirmed",
            "finder": name, "finder_slug": self.slug(sub["user_id"]) if finder and self.is_public(sub["user_id"]) else None, "checkers": checkers,
            "ra": sub["ra"], "dec": sub["dec"], "field_key": sub["field_key"], "submitted": iso(sub["created"]),
            "checks": json.loads(sub["checks"]), "marks": marks, "machine": not finding_id.startswith("user:"),
            "summary": (
                f"{name} found a new moving object in NASA's SPHEREx infrared survey, using {SITE}. It passed every check against the "
                "catalogues and the archives and was confirmed as new." + (f" Checked by {', '.join(checkers)}." if checkers else "")
            ) if status == "confirmed" else None,
            "next": "A confirmed object is reported under the finder's name." if status == "confirmed" else "The team looks at every submitted finding. When the verdict is given, this page says so.",
        }  # fmt: skip

    def edit_story(self, finding_id: str, user_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        if self.story(finding_id, user_id, make=True) is None:
            return None
        c = self.db._conn()
        for k in ("title", "intro", "notes"):
            if changes.get(k) is not None:
                c.execute(
                    f"UPDATE acc_stories SET {k}=?, updated=? WHERE finding_id=? AND user_id=?",
                    (changes[k], time.time(), finding_id, user_id),
                )
        if changes.get("public") is not None:
            pub = bool(changes["public"])
            c.execute(
                "UPDATE acc_stories SET public=?, published=CASE WHEN ? THEN COALESCE(published, ?) ELSE published END, updated=? WHERE finding_id=? AND user_id=?",
                (int(pub), int(pub), time.time(), time.time(), finding_id, user_id),
            )
        return self.story(finding_id, user_id)

    def latest_discovery(self) -> dict[str, Any] | None:
        """The newest confirmed finding with a public story: what the landing page shows as "Latest discovery"."""
        c = self.db._conn()
        r = c.execute(
            "SELECT s.finding_id FROM acc_submissions s JOIN acc_stories t ON t.finding_id=s.finding_id"
            " WHERE s.status='confirmed' AND t.public=1 ORDER BY s.updated DESC LIMIT 1"
        ).fetchone()
        if r is None:
            return None
        s = self.story(r["finding_id"])
        if s is None:
            return None
        ref = r["finding_id"]
        return {"finding_id": ref, "title": s["title"], "finder": s["finder"], "summary": s["summary"], "confirmed": s["updated"],
                "url": f"/s/discovery/{ref}", "page": f"#/findings/{ref}/story", "image": f"/s/discovery/{ref}/image.png"}  # fmt: skip

    # ------------------------------------------------------------------------------------------------------- links and counts

    def target(self, kind: str, ref: str, viewer: str | None = None) -> dict[str, str] | None:
        """What a shared link is about: {title, text (one line), page (the app's hash), badge|finding}. None when it is not public."""
        if kind in ("profile", "achievement"):
            slug, _, badge = ref.partition(".")
            p = self.profile(slug, viewer)
            if p is None:
                return None
            if kind == "achievement":
                a = scoring.BY_ID.get(badge)
                if a is None or not any(b["id"] == badge for b in p["badges"]):
                    return None
                return {"title": f"{p['name']} earned “{a.name}” on {SITE}", "text": f"{a.line}. Searching NASA's infrared sky for things that move.",
                        "page": f"#/u/{slug}?badge={badge}", "badge": badge}  # fmt: skip
            n = len(p["badges"])
            return {"title": f"{p['name']} on {SITE}", "text": f"{p['level']} · {p['points']} points · {n} {'badge' if n == 1 else 'badges'}. Searching NASA's infrared sky for things that move.",
                    "page": f"#/u/{slug}", "badge": f"level_{p['level'].lower()}"}  # fmt: skip
        if kind in ("story", "discovery"):
            s = self.story(ref, viewer)
            if s is None or (kind == "discovery" and not s["discovery"]):
                return None
            if s["discovery"]:
                return {"title": f"Discovery: {s['title']}", "text": f"Found by {s['finder']} in NASA's SPHEREx infrared survey and confirmed as new.",
                        "page": f"#/findings/{ref}/story", "finding": ref}  # fmt: skip
            return {"title": s["title"], "text": f"A moving object found by {s['finder']} in NASA's SPHEREx infrared survey. {s['status_text']}",
                    "page": f"#/findings/{ref}/story", "finding": ref}  # fmt: skip
        return None

    def count(self, kind: str, ref: str, channel: str, what: str) -> None:
        """One more share, visit or preview (a crawler's fetch) of a link; nothing about who."""
        if kind not in KINDS or what not in ("shares", "visits", "previews"):
            return
        channel = channel if channel in CHANNELS else "link"
        c = self.db._conn()
        c.execute("INSERT OR IGNORE INTO acc_share_counts(kind,ref,channel) VALUES(?,?,?)", (kind, ref[:200], channel))
        c.execute(f"UPDATE acc_share_counts SET {what}={what}+1 WHERE kind=? AND ref=? AND channel=?", (kind, ref[:200], channel))

    def counts(self) -> list[dict[str, Any]]:
        rows = self.db._conn().execute("SELECT * FROM acc_share_counts ORDER BY shares + visits DESC, kind, ref")
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------------------------------------------- the picture

    def still_of(self, finding: str) -> Any:
        """The first frame of the object's time-lapse (service.clips), or None when none is made."""
        try:
            import asyncio

            from PIL import Image

            from service.routes_clips import clips_of

            clips = clips_of(self.st)
            path = clips.path(finding) if not finding.startswith("user:") and clips.ready(finding) else None
            if path is None:
                sub = self._submission(finding)
                if sub is None or sub["ra"] is None:
                    return None
                path = asyncio.run(clips.place(float(sub["ra"]), float(sub["dec"]), 320, True, False))
            im = Image.open(path)
            im.seek(0)
            return im.convert("RGB")
        except Exception:  # the picture is an extra: the link works without the still
            return None

    def image(self, kind: str, ref: str) -> bytes | None:
        """The Open Graph picture of a link, PNG 1200 × 630: the badge or the object's still, our mark, the title and one line."""
        t = self.target(kind, ref)
        if t is None:
            return None
        from PIL import Image, ImageDraw

        from service.clips import _font

        w, h = OG_SIZE
        im = Image.new("RGB", (w, h), (4, 11, 16))
        d = ImageDraw.Draw(im)
        for y in range(h):  # the app's dark glass: a soft teal light from the upper left
            k = y / h
            d.line([(0, y), (w, y)], fill=(int(8 + 10 * (1 - k)), int(20 + 22 * (1 - k)), int(27 + 26 * (1 - k))))
        art, box = None, 400
        if "badge" in t and (BADGES / f"{t['badge']}.webp").is_file():
            art = Image.open(BADGES / f"{t['badge']}.webp").convert("RGBA").resize((box, box), Image.Resampling.LANCZOS)
        elif "finding" in t:
            still = self.still_of(t["finding"])
            if still is not None:
                art = still.resize((box, box), Image.Resampling.LANCZOS).convert("RGBA")
                d.rectangle([78, 113, 78 + box + 3, 113 + box + 3], outline=(92, 224, 208), width=2)
        x0 = 80
        if art is not None:
            im.paste(art, (80, 115), art)
            x0 = 80 + box + 60
        # our mark: the ring with the cross, and the name
        cx, cy = x0 + 22, 92
        d.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], outline=(95, 227, 214), width=3)
        d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=(95, 227, 214), width=3)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            d.line([(cx + dx * 13, cy + dy * 13), (cx + dx * 27, cy + dy * 27)], fill=(95, 227, 214), width=3)
        d.text((cx + 40, cy - 18), SITE, font=_font(34), fill=(214, 231, 237))

        def wrap(text: str, px: int, width: int) -> list[str]:
            lines, cur = [], ""
            for word in text.split():
                test = f"{cur} {word}".strip()
                if d.textlength(test, font=_font(px)) <= width or not cur:
                    cur = test
                else:
                    lines.append(cur)
                    cur = word
            return [*lines, cur] if cur else lines

        y = 170
        for line in wrap(t["title"], 54, w - x0 - 70)[:4]:
            d.text((x0, y), line, font=_font(54), fill=(226, 255, 251))
            y += 66
        y += 14
        for line in wrap(t["text"], 30, w - x0 - 70)[:4]:
            d.text((x0, y), line, font=_font(30), fill=(160, 185, 194))
            y += 40
        d.text((x0, h - 70), "www.spherex.space", font=_font(26), fill=(255, 180, 84))
        out = io.BytesIO()
        im.save(out, "PNG", optimize=True)
        return out.getvalue()


def get(st: Any, db: AccountsDB) -> Share:
    s: Share | None = getattr(st, "share", None)
    if s is None or s.db is not db:
        s = st.share = Share(st, db)
    return s
