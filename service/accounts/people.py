# ruff: noqa: E501
"""What people do for the machine (product spec §5e; it replaces the question trees of §5b).

Rule: never ask what the machine already knows (numbers, detector places, catalogue answers, spectrum fits). Ask only what a
human eye judges better, and let every answer change something the machine does.

Question series, one per object kind (`SERIES`; wording is the spec's). A task carries its series; the done body carries the path
of answers, one answer id per step (and, for a step that asks to tap pictures, the tapped picture indexes). "Not sure" at any step
ends the path as "Can't tell", which is never wrong and never paid. The answers of one find settle by agreement: AGREE_WEIGHT
(3.0) of agreeing weight, or MAX_ANSWERS (5) answers given; each person weighs by their accuracy (`weight_of`). Those who agreed
get POINTS["check"] (10); a different answer counts as wrong in the accuracy.

What a settled result does on the server (`ACTIONS`), read by the engine with the read-only functions at the end of this file:
  track    no dot / a streak         → dropped before the slow checks              dropped(st)
           only in some + taps       → re-linked without the tapped pictures        missing_visits(st, finding_id)
  slow     does not shift / two dots → dropped (a blend)                            dropped(st)
           old pictures show it      → up the queue (motion over decades)           raised(st)
  peak     no dot / in a glow        → dropped; "glow" is counted against the rule  dropped(st), counts()
           a dot on the left only    → up the queue (a candidate)                   raised(st)
  red      moved                     → the brown-dwarf search's queue               agreement.brown_dwarf_queue()
  anomaly  a neighbour touches it    → those exposures left out of its spectrum     left_out(st, finding_id)
           the odd pictures broken   → each answer flags those exposures; an exposure is bad after BAD_FLAGS (2) distinct
                                       people                                       bad_exposures(st), people_bad_exposures(st)

More ways people help (task kinds): find_again (tap the lead in the next pass: taps(st, id, "find_again")), recentre (tap the
object's centre in each picture: taps(st, id, "recentre")), bad_picture (a second look at an exposure one person flagged),
second_look (a finding someone else submitted, before the verdict) and talk (comments under a finding).

Control objects with a known answer are mixed into every series (each CONTROL_EVERY-th task of a person): finds the machine
already knows (`CONTROL_EXPECT`), or finds people settled unanimously (GOLD_MIN). A control answer is judged at once.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

from service.accounts.db import AccountsDB, iso
from service.accounts.score import POINTS, Score

NOT_SURE = "not_sure"
CANT_TELL = "cant_tell"
AGREE_WEIGHT = 3.0  # this much agreeing weight settles a find (three people of average accuracy)
MAX_ANSWERS = 5  # … or this many answers in all ("Not sure" counts as given)
WEIGHT_MIN_ANSWERS = 5  # a person weighs 1 until this many of their answers were judged
WEIGHT_RANGE = (0.5, 1.5)  # then 0.5 + their accuracy, within this range
CONTROL_EVERY = 5  # every fifth task of a person in a series is a control object (when one is there)
GOLD_MIN = 3  # a find people settled unanimously (at least this many answers, all the same) is a control object for the others
BAD_FLAGS = 2  # an exposure flagged broken by this many distinct people is left out of every stack and spectrum
RELINK_SHARE = 0.5  # a picture is left out of a track when at least this share of those who said "only in some" tapped it
COMMENT_MAX = 1000  # characters
COMMENT_LINKS = 3  # links in one comment
COMMENT_GAP_S = 10.0  # between two comments of one person
REPORTS_HIDE = 3  # a comment reported by this many distinct people is hidden

#: The question series (spec §5e). A step: id, the question, what the lab shows with it (`show`), the answers. An answer leads
#: to the next step (`next`) or ends the path with a result. A step with `tap` asks to tap pictures: its one answer "Done" goes on,
#: and the tapped picture indexes come in the done body (`pictures`). "Not sure" is added to every step.
SERIES: dict[str, tuple[dict[str, Any], ...]] = {
    "track": (  # moves within one pass; the Blink of its visits
        {"id": "steps", "question": "Does one dot step along the arrow?", "show": "blink", "answers": (
            {"id": "every", "label": "Yes, in every picture", "next": "shape"},
            {"id": "some", "label": "Only in some", "next": "missing"},
            {"id": "no", "label": "No", "result": "no_dot"})},
        {"id": "missing", "question": "Tap the pictures where it is missing.", "show": "pictures", "tap": True, "answers": (
            {"id": "done", "label": "Done", "next": "shape"},)},
        {"id": "shape", "question": "Is it a dot like the stars, or a streak?", "show": "blink", "answers": (
            {"id": "dot", "label": "Dot", "result": "moves"},
            {"id": "streak", "label": "Streak", "result": "streak"})},
    ),
    "slow": (  # a slow mover or a brown-dwarf candidate; the Blink of two passes
        {"id": "shift", "question": "Does the dot in the ring shift while the stars stay put?", "show": "blink2", "answers": (
            {"id": "yes", "label": "Yes", "next": "blend"},
            {"id": "no", "label": "No", "result": "still"})},
        {"id": "blend", "question": "Is it one dot, or two touching?", "show": "blink2", "answers": (
            {"id": "one", "label": "One", "next": "old"},
            {"id": "two", "label": "Two", "result": "blend"})},
        {"id": "old", "question": "Old pictures (WISE 2010, 2MASS 2000): is there a dot along the arrow, further back?", "show": "old_surveys", "answers": (
            {"id": "yes", "label": "Yes", "result": "moves_old"},
            {"id": "no", "label": "No", "result": "moves"})},
    ),
    "peak": (  # a Planet X peak; two sums side by side
        {"id": "left", "question": "Left: added along the orbit. Right: added as it is. Is there a dot in the ring on the left only?", "show": "sums", "answers": (
            {"id": "yes", "label": "Yes", "next": "glow"},
            {"id": "no", "label": "No", "result": "no_peak"})},
        {"id": "glow", "question": "Is it inside a bright star's glow or on its spike?", "show": "picture", "answers": (
            {"id": "yes", "label": "Yes", "result": "glow"},
            {"id": "no", "label": "No", "result": "candidate"})},
    ),
    "red": (
        {"id": "shape", "question": "A sharp dot, or a fuzzy patch?", "show": "picture", "answers": (
            {"id": "sharp", "label": "A sharp dot", "next": "every"},
            {"id": "fuzzy", "label": "A fuzzy patch", "result": "galaxy"})},
        {"id": "every", "question": "Is it there every time?", "show": "passes", "answers": (
            {"id": "yes", "label": "Yes", "next": "glow"},
            {"id": "no", "label": "No", "result": "artefact"})},
        {"id": "glow", "question": "Is it on a bright star's glow, a spike, or the picture edge?", "show": "picture", "answers": (
            {"id": "yes", "label": "Yes", "result": "artefact"},
            {"id": "no", "label": "No", "next": "moved"})},
        {"id": "moved", "question": "Old picture next to new: has it moved?", "show": "old_new", "answers": (
            {"id": "yes", "label": "Yes", "result": "brown_dwarf"},
            {"id": "no", "label": "No", "result": "still"})},
    ),
    "anomaly": (  # the stamps of the odd exposures
        {"id": "neighbour", "question": "In the odd pictures, does a neighbour star touch it?", "show": "stamps", "answers": (
            {"id": "yes", "label": "Yes", "result": "neighbour"},
            {"id": "no", "label": "No", "next": "broken"})},
        {"id": "broken", "question": "Do the odd pictures look broken (a streak, cut off, glare)?", "show": "stamps", "answers": (
            {"id": "yes", "label": "Yes", "result": "broken"},
            {"id": "no", "label": "No", "result": "real"})},
    ),
}  # fmt: skip
#: the series of a find, by the machine's kind of find
SERIES_OF_KIND = {"film": "track", "mover": "slow", "browndwarf": "slow", "peak": "peak", "red": "red", "anomaly": "anomaly"}
RESULTS = {
    "track": {"moves": "A dot steps along the arrow", "partial": "A dot steps along the arrow, not in every picture",
              "no_dot": "No dot steps along the arrow", "streak": "A streak: a satellite or a plane"},
    "slow": {"moves": "It shifts", "moves_old": "It shifts, and old pictures show it further back", "still": "It does not shift",
             "blend": "Two dots touching"},
    "peak": {"candidate": "A dot on the orbit sum only", "no_peak": "No dot on the orbit sum only", "glow": "In a bright star's glow or on its spike"},
    "red": {"galaxy": "A fuzzy patch: a galaxy", "artefact": "Not there every time, or on a glow, a spike or the edge",
            "brown_dwarf": "It moved: a cold brown-dwarf candidate", "still": "A red dot that has not moved"},
    "anomaly": {"neighbour": "A neighbour star touches it", "broken": "The odd pictures look broken", "real": "A real feature"},
}  # fmt: skip
ACTIONS = {
    "track": {"no_dot": "drop", "streak": "drop", "partial": "relink"},
    "slow": {"still": "drop", "blend": "drop", "moves_old": "raise"},
    "peak": {"no_peak": "drop", "glow": "drop", "candidate": "raise"},
    "red": {"brown_dwarf": "bd_queue"},
    "anomaly": {"neighbour": "leave_out"},
}
#: what a person should answer on a find the machine already knows: (series, machine status or stage) → the right results
CONTROL_EXPECT: dict[tuple[str, str], frozenset[str]] = {
    ("track", "known"): frozenset({"moves", "partial"}),
    ("track", "artefact"): frozenset({"no_dot", "streak"}),
    ("slow", "known"): frozenset({"moves", "moves_old"}),
    ("slow", "artefact"): frozenset({"still", "blend"}),
    ("peak", "artefact"): frozenset({"no_peak", "glow"}),
    ("red", "galaxy"): frozenset({"galaxy"}),
    ("red", "residual"): frozenset({"artefact"}),
    ("red", "halo"): frozenset({"artefact"}),
}
#: the answers counted against the machine's own rules (for the Method page): series, result, the rule that let the find through
RULE_COUNTS = (
    ("peak", "glow", "the Planet X search's bright-star glow cut let it through"),
    ("track", "streak", "the time-lapse track search took a streak (a satellite or a plane) for a moving dot"),
    ("slow", "blend", "the mover search took two touching stars for one moving dot"),
)
TASK_KINDS = ("find_again", "recentre", "bad_picture", "second_look")
SECOND_LOOK_ANSWERS = (("moves", "It moves"), ("artefact", "Artefact"), (CANT_TELL, "Can't tell"))
FIND_AGAIN_KINDS = ("film", "peak")  # a lead seen in one pass
RECENTRE_KINDS = ("mover", "browndwarf", "film")
LINK = re.compile(r"https?://[^\s<>\"']+", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS acc_series_answers (
  user_id TEXT NOT NULL,
  series TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  path TEXT NOT NULL,
  result TEXT NOT NULL,
  pictures TEXT NOT NULL DEFAULT '[]',
  exposures TEXT NOT NULL DEFAULT '[]',
  weight REAL NOT NULL,
  control INTEGER NOT NULL DEFAULT 0,
  at REAL NOT NULL,
  settled REAL,
  PRIMARY KEY (user_id, series, finding_id)
);
CREATE INDEX IF NOT EXISTS acc_series_answers_find ON acc_series_answers(series, finding_id);
CREATE TABLE IF NOT EXISTS acc_series_results (
  series TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  result TEXT,
  agree INTEGER NOT NULL,
  agree_weight REAL NOT NULL,
  total INTEGER NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY (series, finding_id)
);
CREATE TABLE IF NOT EXISTS acc_people_actions (
  finding_id TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  at REAL NOT NULL,
  PRIMARY KEY (finding_id, action)
);
CREATE TABLE IF NOT EXISTS acc_exposure_flags (
  exposure TEXT NOT NULL,
  user_id TEXT NOT NULL,
  verdict TEXT NOT NULL,
  source TEXT NOT NULL,
  finding_id TEXT,
  field_key TEXT,
  ra REAL,
  dec REAL,
  at REAL NOT NULL,
  PRIMARY KEY (exposure, user_id)
);
CREATE TABLE IF NOT EXISTS acc_taps (
  kind TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  pass INTEGER,
  frame INTEGER,
  mjd REAL,
  ra REAL NOT NULL,
  dec REAL NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS acc_taps_find ON acc_taps(kind, finding_id);
CREATE TABLE IF NOT EXISTS acc_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  text TEXT NOT NULL,
  at REAL NOT NULL,
  hidden INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS acc_comments_find ON acc_comments(finding_id, at);
CREATE TABLE IF NOT EXISTS acc_comment_reports (
  comment_id INTEGER NOT NULL,
  user_id TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY (comment_id, user_id)
);
"""


class AnswerError(ValueError):
    """An answer the series does not allow; the message is a plain sentence for the person."""


def series_of(find_kind: str | None) -> str | None:
    return SERIES_OF_KIND.get(str(find_kind or ""))


def steps_of(series: str) -> list[dict[str, Any]]:
    """The series as a task carries it: every step ends with "Not sure"."""
    return [
        {**{k: v for k, v in step.items() if k != "answers"},
         "answers": [*(dict(a) for a in step["answers"]), {"id": NOT_SURE, "label": "Not sure", "result": CANT_TELL}]}
        for step in SERIES[series]
    ]  # fmt: skip


def label_of(series: str, result: str | None) -> str:
    if result is None:
        return "no agreement"
    if result == CANT_TELL:
        return "Can't tell"
    return RESULTS.get(series, {}).get(result, result)


def run_path(series: str, path: list[str], pictures: list[int] | None = None) -> str:
    """The result a path of answers leads to (one answer id per step, from the first). Raises AnswerError for an answer the step
    does not have, or a path that stops before its end or goes on after it. A tap step answered with no picture tapped means the
    object is in every picture."""
    steps = {s["id"]: s for s in SERIES[series]}
    step: dict[str, Any] | None = SERIES[series][0]
    went_some = False
    for i, answer in enumerate(path):
        if step is None:
            raise AnswerError("the answers go on after the last question")
        if answer == NOT_SURE:
            if i != len(path) - 1:
                raise AnswerError("the answers go on after “Not sure”")
            return CANT_TELL
        chosen = next((a for a in step["answers"] if a["id"] == answer), None)
        if chosen is None:
            raise AnswerError(f"“{answer}” is not an answer to “{step['question']}”")
        went_some = went_some or (series == "track" and step["id"] == "steps" and answer == "some")
        if step.get("tap") and not pictures:
            # "Done" with none tapped: it is missing from none of them, i.e. it is in every picture (a valid answer, not an error)
            went_some = False
        if "result" in chosen:
            if i != len(path) - 1:
                raise AnswerError("the answers go on after the last question")
            result = str(chosen["result"])
            return "partial" if result == "moves" and went_some else result
        step = steps[chosen["next"]]
    raise AnswerError("the answers stop before the last question")


def picture_list(find: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The pictures of a track to tap (its visits): index, date and place."""
    visits = ((find or {}).get("data") or {}).get("visits") or []
    return [{"i": i, "mjd": v.get("mjd"), "ra": v.get("ra"), "dec": v.get("dec")} for i, v in enumerate(visits) if isinstance(v, dict)]


class People:
    def __init__(self, score: Score) -> None:
        self.score = score
        self.db: AccountsDB = score.db
        self.db._conn().executescript(SCHEMA)
        from service.accounts import agreement  # the brown-dwarf queue's table lives there

        self.agreement = agreement.Agreement(score)

    # -------------------------------------------------------------------------------------------------------------- weights

    def weight_of(self, user_id: str) -> float:
        """1 until WEIGHT_MIN_ANSWERS of the person's answers were judged; then 0.5 + their accuracy, within WEIGHT_RANGE."""
        n, right = (
            self.db._conn()
            .execute("SELECT COUNT(right), COALESCE(SUM(right), 0) FROM acc_score_events WHERE user_id=? AND right IS NOT NULL", (user_id,))
            .fetchone()
        )
        if int(n) < WEIGHT_MIN_ANSWERS:
            return 1.0
        return max(WEIGHT_RANGE[0], min(WEIGHT_RANGE[1], 0.5 + float(right) / float(n)))

    # ------------------------------------------------------------------------------------------------------------ choosing

    def answered_by(self, user_id: str, series: str) -> set[str]:
        rows = self.db._conn().execute("SELECT finding_id FROM acc_series_answers WHERE user_id=? AND series=?", (user_id, series))
        return {r[0] for r in rows}

    def settled_ids(self, series: str) -> set[str]:
        return {r[0] for r in self.db._conn().execute("SELECT finding_id FROM acc_series_results WHERE series=?", (series,))}

    def gold(self, series: str) -> dict[str, frozenset[str]]:
        """Finds people settled unanimously: control objects for the others."""
        rows = self.db._conn().execute(
            "SELECT finding_id, result FROM acc_series_results WHERE series=? AND result IS NOT NULL AND agree>=? AND agree=total",
            (series, GOLD_MIN),
        )
        return {r["finding_id"]: frozenset({r["result"]}) for r in rows}

    def expected(self, series: str, row: dict[str, Any]) -> frozenset[str] | None:
        """The right results of a control object, or None when the find is not one."""
        got = CONTROL_EXPECT.get((series, str(row.get("status")))) or CONTROL_EXPECT.get((series, str(row.get("stage"))))
        return got if got is not None else self.gold(series).get(row["finding_id"])

    def pick(
        self,
        task_kind: str,
        rows: list[dict[str, Any]],
        user_id: str | None,
        seen: set[str],
        controls: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any] | None:
        """The next find to ask about in a series task. `task_kind` is moves (tracks, slow movers, peaks), red, anomaly or finding
        (any). An unexplained find nobody settled, one already answered by others first (it is the nearest to a result); every
        CONTROL_EVERY-th task of a person, a control object instead, from `rows` or the extra `controls` (the hidden finds, asked for
        only then: the task route reads them with a second pass over every find)."""
        kinds = {"moves": ("film", "mover", "browndwarf", "peak"), "red": ("red",), "anomaly": ("anomaly",)}.get(task_kind)
        pool = [r for r in rows if series_of(r.get("kind")) and (kinds is None or r.get("kind") in kinds)]
        mine = set(seen)
        done_n = 0
        for s in {series_of(r.get("kind")) for r in pool}:
            assert s is not None
            if user_id:
                got = self.answered_by(user_id, s)
                mine |= got
                done_n += len(got)
        if user_id and done_n % CONTROL_EVERY == CONTROL_EVERY - 1:
            extra = [r for r in (controls() if controls else []) if series_of(r.get("kind")) and (kinds is None or r.get("kind") in kinds)]
            cands = [r for r in pool + extra if r["finding_id"] not in mine and self.expected(str(series_of(r.get("kind"))), r) is not None]
            if cands:
                return cands[0]
        settled: set[str] = set()
        for s in {series_of(r.get("kind")) for r in pool}:
            settled |= self.settled_ids(str(s))
        counts = Counter(r[0] for r in self.db._conn().execute("SELECT finding_id FROM acc_series_answers WHERE settled IS NULL"))
        open_ = [r for r in pool if r.get("stage") is None and r["finding_id"] not in mine and r["finding_id"] not in settled]
        open_.sort(key=lambda r: -counts.get(r["finding_id"], 0))  # stable: the Findings page's order among equals
        return open_[0] if open_ else None

    # ------------------------------------------------------------------------------------------------------------- answers

    def answer(
        self, user_id: str, series: str, row: dict[str, Any], path: list[str], pictures: list[int], exposures: list[str]
    ) -> tuple[str, int]:
        """Record a person's path through a series (the first answer stays) and act on it. Returns (result, points paid now)."""
        result = run_path(series, path, pictures)
        fid = row["finding_id"]
        c = self.db._conn()
        expect = self.expected(series, row)
        control = expect is not None
        cur = c.execute(
            "INSERT OR IGNORE INTO acc_series_answers(user_id,series,finding_id,path,result,pictures,exposures,weight,control,at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, series, fid, json.dumps(path), result, json.dumps(sorted(set(pictures))), json.dumps(sorted(set(exposures))),
             self.weight_of(user_id), int(control), time.time()),
        )  # fmt: skip
        if cur.rowcount == 0:
            return result, 0  # answered before: the first answer stays
        before = self._paid(user_id)
        if series == "anomaly" and result == "broken":  # each person's "the odd pictures look broken" is a flag of those exposures
            for e in exposures:
                self.flag(
                    user_id, e, "broken", "anomaly", finding_id=fid, ra=row.get("ra"), dec=row.get("dec"), field_key=row.get("field_key")
                )
        if control:
            if result != CANT_TELL:
                right = result in (expect or frozenset())
                self.score._event(
                    user_id, f"control_{series}", fid, POINTS["check"] if right else 0, right, {"answer": result, "series": series}
                )
            c.execute(
                "UPDATE acc_series_answers SET settled=? WHERE user_id=? AND series=? AND finding_id=?", (time.time(), user_id, series, fid)
            )
        else:
            self.settle(series, row)
        self.score.award(user_id)
        return result, self._paid(user_id) - before

    def _paid(self, user_id: str) -> int:
        r = self.db._conn().execute(
            "SELECT COALESCE(SUM(points),0) FROM acc_score_events WHERE user_id=? AND (kind='series' OR kind LIKE 'control_%')", (user_id,)
        )
        return int(r.fetchone()[0])

    def settle(self, series: str, row: dict[str, Any]) -> dict[str, Any] | None:
        """Store the people's result of a find once AGREE_WEIGHT of weight agrees or MAX_ANSWERS answers are given; pay those who
        agreed; do what the result says (ACTIONS)."""
        fid = row["finding_id"]
        c = self.db._conn()
        if c.execute("SELECT 1 FROM acc_series_results WHERE series=? AND finding_id=?", (series, fid)).fetchone():
            self._judge(series, fid)  # a late answer is judged against the stored result
            return self.results([fid]).get(fid)
        rows = c.execute(
            "SELECT * FROM acc_series_answers WHERE series=? AND finding_id=? AND control=0 ORDER BY at", (series, fid)
        ).fetchall()
        weights: dict[str, float] = defaultdict(float)
        people: Counter[str] = Counter()
        for r in rows:
            if r["result"] != CANT_TELL:
                weights[r["result"]] += float(r["weight"])
                people[r["result"]] += 1
        ranked = sorted(weights.items(), key=lambda kv: -kv[1])
        top_w = ranked[0][1] if ranked else 0.0
        if top_w < AGREE_WEIGHT and len(rows) < MAX_ANSWERS:
            return None
        clear = bool(ranked) and (len(ranked) == 1 or ranked[0][1] > ranked[1][1])
        result = ranked[0][0] if clear else None
        c.execute(
            "INSERT OR IGNORE INTO acc_series_results(series,finding_id,result,agree,agree_weight,total,at) VALUES(?,?,?,?,?,?,?)",
            (series, fid, result, people[result] if result else 0, round(top_w if clear else 0.0, 3), len(rows), time.time()),
        )
        if result is not None:
            self._act(series, fid, result, rows, row)
        self._judge(series, fid)
        return self.results([fid]).get(fid)

    def _act(self, series: str, fid: str, result: str, rows: list[Any], row: dict[str, Any]) -> None:
        action = ACTIONS.get(series, {}).get(result)
        if action is None:
            return
        c = self.db._conn()
        data: dict[str, Any] = {}
        if action == "relink":
            said = [r for r in rows if r["result"] == "partial"]
            tapped = Counter(i for r in said for i in json.loads(r["pictures"]))
            data["missing"] = sorted(i for i, n in tapped.items() if n >= max(1.0, RELINK_SHARE * len(said)))
            if not data["missing"]:
                return  # nobody agrees on which pictures: the track stays as the machine linked it
        elif action == "leave_out":
            data["exposures"] = sorted({e for r in rows if r["result"] == "neighbour" for e in json.loads(r["exposures"])})
            if not data["exposures"]:
                return
        elif action == "bd_queue":
            c.execute(
                "INSERT OR IGNORE INTO acc_bd_queue(finding_id,ra,dec,agree,total,at) VALUES(?,?,?,?,?,?)",
                (fid, row.get("ra"), row.get("dec"), sum(1 for r in rows if r["result"] == result), len(rows), time.time()),
            )
        c.execute(
            "INSERT OR REPLACE INTO acc_people_actions(finding_id,action,reason,data,at) VALUES(?,?,?,?,?)",
            (fid, action, result, json.dumps(data), time.time()),
        )

    def _judge(self, series: str, fid: str) -> None:
        c = self.db._conn()
        said = c.execute("SELECT result FROM acc_series_results WHERE series=? AND finding_id=?", (series, fid)).fetchone()
        if said is None:
            return
        for r in c.execute(
            "SELECT * FROM acc_series_answers WHERE series=? AND finding_id=? AND settled IS NULL AND control=0", (series, fid)
        ).fetchall():
            if r["result"] != CANT_TELL and said["result"] is not None:  # with no agreement nobody is right or wrong
                right = r["result"] == said["result"]
                self.score._event(r["user_id"], "series", f"{series}:{fid}", POINTS["check"] if right else 0, right,
                                  {"series": series, "answer": r["result"], "result": said["result"], "answered_at": r["at"]})  # fmt: skip
            c.execute(
                "UPDATE acc_series_answers SET settled=? WHERE user_id=? AND series=? AND finding_id=?",
                (time.time(), r["user_id"], series, fid),
            )
            self.score.award(r["user_id"])

    def results(self, finding_ids: list[str]) -> dict[str, dict[str, Any]]:
        """{find id: {series, result, label, agree, total, text}}: what people said ("people say: a dot steps along the arrow, 3 of 4")."""
        if not finding_ids:
            return {}
        q = f"SELECT * FROM acc_series_results WHERE finding_id IN ({','.join('?' * len(finding_ids))})"
        out = {}
        for r in self.db._conn().execute(q, finding_ids):
            label = label_of(r["series"], r["result"])
            text = (
                f"people say: {label[0].lower() + label[1:]}, {r['agree']} of {r['total']}"
                if r["result"]
                else f"people do not agree ({r['total']} answers)"
            )
            out[r["finding_id"]] = {"series": r["series"], "kind": r["series"], "result": r["result"], "label": label, "agree": r["agree"],
                                    "total": r["total"], "text": text, "at": iso(r["at"])}  # fmt: skip
        return out

    def set_aside_results(self, finding_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Machine finds people set aside with their artefact marks (spec §5g): "people say: artefact, 2 of 2"."""
        if not finding_ids:
            return {}
        q = f"SELECT finding_id, data, at FROM acc_people_actions WHERE action='set_aside' AND finding_id IN ({','.join('?' * len(finding_ids))})"
        out = {}
        for r in self.db._conn().execute(q, finding_ids):
            d = json.loads(r["data"])
            n, total = int(d.get("agree") or 2), int(d.get("total") or 2)
            out[r["finding_id"]] = {"series": "marks", "kind": "artefact", "result": "artefact", "label": "Artefact", "agree": n, "total": total,
                                    "look": d.get("look"), "set_aside": True, "text": f"people say: artefact, {n} of {total}", "at": iso(r["at"])}  # fmt: skip
        return out

    # ----------------------------------------------------------------------------------------------- the other task kinds

    def flag(self, user_id: str, exposure: str, verdict: str, source: str, **where: Any) -> int:
        """A person says an exposure is broken (or, on a second look, fine). One record per person and exposure: the last word
        stays. Returns how many distinct people call it broken now."""
        c = self.db._conn()
        c.execute(
            "INSERT INTO acc_exposure_flags(exposure,user_id,verdict,source,finding_id,field_key,ra,dec,at) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(exposure,user_id) DO UPDATE SET verdict=excluded.verdict, source=excluded.source, at=excluded.at",
            (
                exposure,
                user_id,
                verdict,
                source,
                where.get("finding_id"),
                where.get("field_key"),
                where.get("ra"),
                where.get("dec"),
                time.time(),
            ),
        )
        return int(c.execute("SELECT COUNT(*) FROM acc_exposure_flags WHERE exposure=? AND verdict='broken'", (exposure,)).fetchone()[0])

    def pick_exposure(self, user_id: str | None) -> dict[str, Any] | None:
        """bad_picture: an exposure exactly one other person called broken: a second, independent look."""
        c = self.db._conn()
        rows = c.execute(
            "SELECT exposure, MIN(field_key) field_key, MIN(ra) ra, MIN(dec) dec, MIN(finding_id) finding_id, COUNT(*) n FROM acc_exposure_flags"
            " WHERE verdict='broken' GROUP BY exposure HAVING n=1 ORDER BY MIN(at)"
        ).fetchall()
        for r in rows:
            if (
                user_id
                and c.execute("SELECT 1 FROM acc_exposure_flags WHERE exposure=? AND user_id=?", (r["exposure"], user_id)).fetchone()
            ):
                continue
            return {"finding_id": r["exposure"], "exposure": r["exposure"], "field_key": r["field_key"], "ra": r["ra"], "dec": r["dec"],
                    "of_finding": r["finding_id"]}  # fmt: skip
        return None

    def tap(self, kind: str, finding_id: str, user_id: str, taps: list[dict[str, Any]]) -> int:
        """find_again / recentre: the places a person tapped (per pass or per picture). The first set of a person stays."""
        c = self.db._conn()
        if c.execute("SELECT 1 FROM acc_taps WHERE kind=? AND finding_id=? AND user_id=?", (kind, finding_id, user_id)).fetchone():
            return 0
        now = time.time()
        c.executemany(
            "INSERT INTO acc_taps(kind,finding_id,user_id,pass,frame,mjd,ra,dec,at) VALUES(?,?,?,?,?,?,?,?,?)",
            [(kind, finding_id, user_id, t.get("pass"), t.get("frame"), t.get("mjd"), float(t["ra"]), float(t["dec"]), now) for t in taps],
        )
        return len(taps)

    def pick_second_look(self, user_id: str | None) -> dict[str, Any] | None:
        """second_look: a finding someone else submitted that waits for the verdict, the least looked at first."""
        c = self.db._conn()
        rows = c.execute(
            "SELECT s.finding_id, s.ra, s.dec, s.field_key, s.title, (SELECT COUNT(*) FROM acc_answers a WHERE a.finding_id=s.finding_id) n"
            " FROM acc_submissions s WHERE s.status IN ('submitted','in_review') AND s.user_id!=? ORDER BY n, s.created",
            (user_id or "",),
        ).fetchall()
        for r in rows:
            if user_id and c.execute("SELECT 1 FROM acc_answers WHERE user_id=? AND finding_id=?", (user_id, r["finding_id"])).fetchone():
                continue
            return {"finding_id": r["finding_id"], "ra": r["ra"], "dec": r["dec"], "field_key": r["field_key"], "title": r["title"]}
        return None

    # ---------------------------------------------------------------------------------------------------------------- talk

    def comments(self, finding_id: str, viewer: str | None = None) -> list[dict[str, Any]]:
        """The comments under a finding, oldest first: the author's public name only; hidden ones are left out (their author
        still sees their own)."""
        rows = self.db._conn().execute(
            "SELECT c.*, u.name, (SELECT COUNT(*) FROM acc_comment_reports r WHERE r.comment_id=c.id) reports FROM acc_comments c"
            " JOIN acc_users u ON u.id=c.user_id WHERE c.finding_id=? ORDER BY c.at, c.id",
            (finding_id,),
        )
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "text": r["text"],
                "at": iso(r["at"]),
                "mine": r["user_id"] == viewer,
                "hidden": bool(r["hidden"]),
            }
            for r in rows
            if not r["hidden"] or r["user_id"] == viewer
        ]

    def add_comment(self, user_id: str, finding_id: str, text: str, gap: bool = True) -> dict[str, Any]:
        text = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
        if not text:
            raise AnswerError("write something first")
        if len(text) > COMMENT_MAX:
            raise AnswerError(f"a comment is at most {COMMENT_MAX} characters")
        if "<" in text and re.search(r"<\s*/?\s*[a-z!]", text, re.I):
            raise AnswerError("plain text only: no HTML")
        if re.search(r"!\[[^\]]*\]\(", text) or re.search(r"data:image/", text, re.I):
            raise AnswerError("no images in comments: links only")
        if len(LINK.findall(text)) > COMMENT_LINKS:
            raise AnswerError(f"at most {COMMENT_LINKS} links in one comment")
        c = self.db._conn()
        last = c.execute("SELECT MAX(at) FROM acc_comments WHERE user_id=?", (user_id,)).fetchone()[0]
        if gap and last is not None and time.time() - float(last) < COMMENT_GAP_S:
            raise AnswerError("wait a few seconds before the next comment")
        cur = c.execute("INSERT INTO acc_comments(finding_id,user_id,text,at) VALUES(?,?,?,?)", (finding_id, user_id, text, time.time()))
        return next(x for x in self.comments(finding_id, user_id) if x["id"] == cur.lastrowid)

    def delete_comment(self, user_id: str, comment_id: int) -> bool:
        return self.db._conn().execute("DELETE FROM acc_comments WHERE id=? AND user_id=?", (comment_id, user_id)).rowcount == 1

    def report(self, user_id: str, comment_id: int) -> bool | None:
        """One report per person; hidden after REPORTS_HIDE distinct reports. None for an unknown comment; a person cannot report
        their own."""
        c = self.db._conn()
        r = c.execute("SELECT user_id FROM acc_comments WHERE id=?", (comment_id,)).fetchone()
        if r is None or r["user_id"] == user_id:
            return None
        c.execute("INSERT OR IGNORE INTO acc_comment_reports(comment_id,user_id,at) VALUES(?,?,?)", (comment_id, user_id, time.time()))
        n = c.execute("SELECT COUNT(*) FROM acc_comment_reports WHERE comment_id=?", (comment_id,)).fetchone()[0]
        if int(n) >= REPORTS_HIDE:
            c.execute("UPDATE acc_comments SET hidden=1 WHERE id=?", (comment_id,))
            return True
        return False

    # ------------------------------------------------------------------------------------------------------------- counts

    def counts(self) -> dict[str, Any]:
        """For the Method page: how often each answer and each result was given, per series, and the answers that go against a
        rule of the machine (how often "glow", "streak", "two touching" were said, of how many answers)."""
        c = self.db._conn()
        out: dict[str, Any] = {"series": {}, "rules": []}
        for series in SERIES:
            rows = c.execute("SELECT path, result, control FROM acc_series_answers WHERE series=?", (series,)).fetchall()
            steps: dict[str, Counter[str]] = defaultdict(Counter)
            for r in rows:
                for step, answer in zip(SERIES[series], json.loads(r["path"]), strict=False):
                    steps[step["id"]][answer] += 1
            settled = Counter(r[0] or "no agreement" for r in c.execute("SELECT result FROM acc_series_results WHERE series=?", (series,)))
            out["series"][series] = {
                "answers": len(rows), "controls": sum(1 for r in rows if r["control"]),
                "results": dict(Counter(r["result"] for r in rows)), "steps": {k: dict(v) for k, v in steps.items()}, "settled": dict(settled),
            }  # fmt: skip
        for series, result, rule in RULE_COUNTS:
            n = out["series"][series]["answers"] - out["series"][series]["controls"]
            said = c.execute(
                "SELECT COUNT(*) FROM acc_series_answers WHERE series=? AND result=? AND control=0", (series, result)
            ).fetchone()[0]
            agreed = c.execute("SELECT COUNT(*) FROM acc_series_results WHERE series=? AND result=?", (series, result)).fetchone()[0]
            out["rules"].append({"series": series, "said": result, "label": label_of(series, result), "rule": rule, "answers": n, "times": int(said),
                                 "share": None if n == 0 else round(int(said) / n, 3), "settled": int(agreed)})  # fmt: skip
        flags = c.execute("SELECT COUNT(DISTINCT exposure) FROM acc_exposure_flags WHERE verdict='broken'").fetchone()[0]
        out["exposures"] = {"flagged": int(flags), "bad": len(_bad(self.db))}
        out["taps"] = {k: int(n) for k, n in c.execute("SELECT kind, COUNT(DISTINCT finding_id || user_id) FROM acc_taps GROUP BY kind")}
        return out


def get(st: Any, db: AccountsDB) -> People:
    from service.accounts import score

    p: People | None = getattr(st, "people", None)
    if p is None or p.db is not db:
        p = st.people = People(score.get(st, db))
    return p


# --------------------------------------------------------------------------------------------------------------------------
# Read-only calls for the engine (prod-build): the names are the contract, not the tables.


def _db(st: Any) -> AccountsDB:
    from service import accounts

    db = accounts.get(st).db
    get(st, db)  # the tables exist
    return db


def _bad(db: AccountsDB) -> set[str]:
    rows = db._conn().execute(
        "SELECT exposure FROM acc_exposure_flags WHERE verdict='broken' GROUP BY exposure HAVING COUNT(*)>=?", (BAD_FLAGS,)
    )
    return {r[0] for r in rows}


def people_bad_exposures(st: Any) -> dict[str, int]:
    """Exposure key → how many distinct people called it broken ("This picture is broken", or an anomaly's "the odd pictures look
    broken"). The keys are exactly as the lab sent them."""
    rows = _db(st)._conn().execute("SELECT exposure, COUNT(*) n FROM acc_exposure_flags WHERE verdict='broken' GROUP BY exposure")
    return {r["exposure"]: int(r["n"]) for r in rows}


def bad_exposures(st: Any) -> set[str]:
    """The exposures to leave out of every stack and spectrum: called broken by at least BAD_FLAGS distinct people."""
    return _bad(_db(st))


def _action(st: Any, action: str) -> dict[str, dict[str, Any]]:
    rows = _db(st)._conn().execute("SELECT finding_id, reason, data FROM acc_people_actions WHERE action=?", (action,))
    return {r["finding_id"]: {"reason": r["reason"], **json.loads(r["data"])} for r in rows}


def left_out(st: Any, finding_id: str) -> set[str]:
    """The exposures to leave out of this find's spectrum (people agreed that a neighbour star touches it in them)."""
    return set(_action(st, "leave_out").get(finding_id, {}).get("exposures", []))


def missing_visits(st: Any, finding_id: str) -> set[int]:
    """Indexes into the find's data["visits"] to re-link the track without (people agreed it is missing there)."""
    return {int(i) for i in _action(st, "relink").get(finding_id, {}).get("missing", [])}


def dropped(st: Any) -> dict[str, str]:
    """Find id → why people dropped it (no_dot, streak, still, blend, no_peak, glow): drop it before the slow checks."""
    return {k: v["reason"] for k, v in _action(st, "drop").items()}


def raised(st: Any) -> dict[str, str]:
    """Find id → why people raised it up the queue (moves_old: old pictures confirm the motion; candidate: a Planet X dot)."""
    return {k: v["reason"] for k, v in _action(st, "raise").items()}


def taps(st: Any, finding_id: str, kind: str) -> list[dict[str, Any]]:
    """The places people tapped: kind find_again (the lead in another pass) or recentre (the object's centre in a picture).
    `user` is an opaque id (never a name or an address), so taps of one person can be told apart."""
    rows = _db(st)._conn().execute("SELECT * FROM acc_taps WHERE kind=? AND finding_id=? ORDER BY at, rowid", (kind, finding_id))
    return [
        {
            "pass": r["pass"],
            "frame": r["frame"],
            "mjd": r["mjd"],
            "ra": r["ra"],
            "dec": r["dec"],
            "user": r["user_id"][-8:],
            "at": iso(r["at"]),
        }
        for r in rows
    ]
