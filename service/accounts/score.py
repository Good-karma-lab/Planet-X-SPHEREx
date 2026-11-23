# ruff: noqa: E501
"""Score, achievements and the leaderboard (product spec §5a). The rule behind every number: reward being right, not being busy.

Every constant and every achievement is in this file (the Method page lists them from here).

Points (signed-in users; each thing counts once per user; nothing is ever taken away):
  square     5     a quarter (10′) of a square looked through: the lab open on it for at least MIN_LOOK_S seconds and every pass
                   shown; a whole square is worth 20; at most DAILY_SQUARES squares' worth (400 quarters) a day (UTC) earn points
  check      10    a machine finding answered, paid when the finding's stage is final and the answer agrees with it (0 otherwise)
  control    25    a known mover marked in a control square without being told (within CONTROL_RADIUS_ARCSEC of its place)
  submit     100   a finding the server submitted (it passed every check)
  candidate  500   a submitted finding that became `candidate` by verdict
  confirmed  5000  a submitted finding that became `confirmed` by verdict
No points for a mark, a note, or a submit that fails the checks. A wrong answer lowers the accuracy instead: the share of the
user's settled answers and control squares that were right.

Tables (the product database): acc_score_events (what earned points, or was right or wrong), acc_answers (answers waiting for a
final stage), acc_verdicts (the stage given by the verdict tool), acc_achievements (earned once).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from service.accounts.db import AccountsDB, iso

# ------------------------------------------------------------------------------------------------------------------ constants

POINTS = {"square": 5, "check": 10, "control": 25, "submit": 100, "candidate": 500, "confirmed": 5000}
MIN_LOOK_S = 60  # a real look at a square: the lab open on it at least this long …
DAILY_SQUARES = 100  # … and at most this many squares' worth a day (UTC) earn points: 400 quarters
QUARTER_DEG = 1.0 / 6.0  # a quarter of a square: 10′; people look through a square a quarter at a time
CONTROL_RADIUS_ARCSEC = 60.0  # a mark this close to the known mover's place (at the survey's epoch) catches it
CONTROL_MIN_PM_ARCSEC_YR = 1.0  # a test target is a control mover when it moves at least this fast
CONTROL_EPOCH = 2025.9  # the middle of the SPHEREx passes: where the control mover is put
RANK_MIN_ANSWERS = 10  # the leaderboard ranks a user only after this many settled answers
RARITY_MIN_SEARCHERS = 50  # "N% of searchers have it" is shown only from this many searchers (a share of two people says nothing)
LEVELS = ((0, "Observer"), (100, "Searcher"), (500, "Tracker"), (2500, "Hunter"), (10000, "Discoverer"))
FINAL_STAGES = ("known", "artefact", "candidate", "confirmed")
#: the answer of the task "Check a machine finding" and the final stages it agrees with
AGREES = {"known": ("known",), "artefact": ("artefact",), "moves": ("candidate", "confirmed")}
#: the stage a machine finding has by its catalogue status alone (service.findings.STAGE_OF_STATUS); other statuses are not final
STAGE_OF_STATUS = {
    "known": "known",
    "residual": "artefact",
    "galaxy": "artefact",
    "below": "artefact",
    "halo": "artefact",
    "set_aside": "artefact",
    "blend": "artefact",
}
GALACTIC_PLANE_DEG = 5.0
ECLIPTIC_DEG = 10.0  # the grid's ecliptic band
P9_RA, P9_BETA = (15.0, 105.0), (-25.0, 15.0)  # the predicted Planet Nine band (service.grid, Brown & Batygin 2021)
POLAR_DEC = 80.0
CELL_DEG = 1.0 / 3.0  # a 20′ square
#: places that name an achievement (JPL Horizons, geocentric, ICRF): Pluto on 23 Jan 1930 (Tombaugh's first plate of the discovery
#: pair, near δ Geminorum); Eris, Makemake and Sedna on 1 Nov 2025
PLUTO_1930 = (110.78300, 21.82622)
FAR_WORLDS = {"Eris": (26.75081, -0.41036), "Makemake": (201.43242, 20.08623), "Sedna": (61.49322, 8.64625)}
NIGHT_HOURS = (0, 4)  # local time: 00:00 up to 04:00

SCHEMA = """
CREATE TABLE IF NOT EXISTS acc_score_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  points INTEGER NOT NULL,
  right INTEGER,
  at REAL NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  UNIQUE (user_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS acc_score_events_at ON acc_score_events(at);
CREATE TABLE IF NOT EXISTS acc_answers (
  user_id TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  answer TEXT NOT NULL,
  at REAL NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  settled REAL,
  PRIMARY KEY (user_id, finding_id)
);
CREATE TABLE IF NOT EXISTS acc_verdicts (
  finding_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS acc_achievements (
  user_id TEXT NOT NULL,
  id TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY (user_id, id)
);
"""

# ------------------------------------------------------------------------------------------------------------------ geometry


def sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a1, d1, a2, d2 = (math.radians(x) for x in (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((a2 - a1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600.0


def ecliptic_lat(ra: float, dec: float) -> float:
    eps, a, d = math.radians(23.4392911), math.radians(ra), math.radians(dec)
    return math.degrees(math.asin(max(-1.0, min(1.0, math.sin(d) * math.cos(eps) - math.cos(d) * math.sin(eps) * math.sin(a)))))


def galactic_lat(ra: float, dec: float) -> float:
    a, d = math.radians(ra), math.radians(dec)
    a0, d0 = math.radians(192.85948), math.radians(27.12825)  # the north galactic pole, J2000
    return math.degrees(math.asin(max(-1.0, min(1.0, math.sin(d) * math.sin(d0) + math.cos(d) * math.cos(d0) * math.cos(a - a0)))))


def in_square(ra: float, dec: float, place: tuple[float, float], size: float = CELL_DEG) -> bool:
    """True when `place` lies in the square of side `size` (degrees; a 20′ square by default) centred on (ra, dec)."""
    half = size / 2 + 1e-6
    dra = abs((place[0] - ra + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec))
    return abs(place[1] - dec) <= half and dra <= half


def place_facts(ra: float | None, dec: float | None, size: float = CELL_DEG) -> dict[str, Any]:
    """What the place rules need to know about a square's centre."""
    if ra is None or dec is None:
        return {}
    beta = ecliptic_lat(ra, dec)
    return {
        "ra": round(ra, 5),
        "dec": round(dec, 5),
        "p9": P9_RA[0] <= ra <= P9_RA[1] and P9_BETA[0] <= beta <= P9_BETA[1],
        "ecliptic": abs(beta) <= ECLIPTIC_DEG,
        "galactic": abs(galactic_lat(ra, dec)) <= GALACTIC_PLANE_DEG,
        "polar": abs(dec) > POLAR_DEC,
        "hour": int(ra // 15) % 24,
        "tombaugh": in_square(ra, dec, PLUTO_1930, size),
        "far_world": next((n for n, p in FAR_WORLDS.items() if in_square(ra, dec, p, size)), None),
    }


def control_movers(targets_file: Path | None = None) -> list[dict[str, Any]]:
    """The first control set: the test targets of data/targets.json that move at least CONTROL_MIN_PM_ARCSEC_YR, each put at its
    place at CONTROL_EPOCH. A square that holds one is a control square; it looks like any other square."""
    path = targets_file or Path(__file__).resolve().parent.parent.parent / "data" / "targets.json"
    try:
        targets = json.loads(path.read_text())["targets"]
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for t in targets:
        pmra, pmdec = float(t.get("pmra_mas_yr") or 0.0) / 1000.0, float(t.get("pmdec_mas_yr") or 0.0) / 1000.0
        if math.hypot(pmra, pmdec) < CONTROL_MIN_PM_ARCSEC_YR:
            continue
        dt = CONTROL_EPOCH - float(t.get("epoch") or 2000.0)
        dec = float(t["dec_deg"]) + pmdec * dt / 3600.0
        ra = float(t["ra_deg"]) + pmra * dt / 3600.0 / max(1e-6, math.cos(math.radians(dec)))
        out.append({"name": t["label"], "ra": ra % 360.0, "dec": dec})
    return out


# -------------------------------------------------------------------------------------------------------------- achievements


@dataclass
class Stats:
    """Everything the achievement rules look at, counted from the user's events."""

    squares: int = 0  # whole squares: all four quarters looked through (or one look of before the quarters)
    quarters: int = 0
    marks: int = 0
    answers: int = 0  # findings answered (settled or not)
    named: bool = False
    control: int = 0
    targets: set[str] = field(default_factory=set)
    settled: int = 0  # answers and control squares that are right or wrong
    right: int = 0
    run: int = 0  # the longest run of right answers
    known_right: int = 0
    jpl_right: int = 0
    artefact_right: int = 0
    peer_right: int = 0
    far_world: bool = False
    p9: int = 0
    ecliptic: int = 0
    north: bool = False
    south: bool = False
    polar: bool = False
    galactic: int = 0
    hours: set[int] = field(default_factory=set)
    first_eyes: int = 0
    inv_notes: bool = False
    measurer: bool = False
    time_traveller: bool = False
    closed: int = 0
    submits: int = 0
    clean: bool = False
    patient: bool = False
    candidates: int = 0
    confirmed: int = 0
    days_run: int = 0
    weekends: int = 0
    top_ten_week: bool = False
    first_week: bool = False
    first_month: bool = False
    tombaugh: bool = False
    night_owl: bool = False
    second_opinion: bool = False

    @property
    def accuracy(self) -> float | None:
        return None if self.settled == 0 else self.right / self.settled


Rule = Callable[[Stats], tuple[float, float]]


def count(name: str, need: int) -> Rule:
    return lambda s: (float(getattr(s, name)), float(need))


def flag(name: str) -> Rule:
    return lambda s: (1.0 if getattr(s, name) else 0.0, 1.0)


def _nine_of_ten(s: Stats) -> tuple[float, float]:
    ok = s.accuracy is not None and s.accuracy >= 0.9
    return (float(s.settled) if ok else float(min(s.settled, 99)), 100.0)


@dataclass(frozen=True)
class Achievement:
    id: str
    family: str
    tier: int
    name: str
    line: str
    rule: Rule
    hidden: bool = False


def _a(id_: str, family: str, tier: int, name: str, line: str, rule: Rule, hidden: bool = False) -> Achievement:
    return Achievement(id_, family, tier, name, line, rule, hidden)


#: every achievement, as data: adding one is one entry (id, family, tier, name, line, rule)
ACHIEVEMENTS: tuple[Achievement, ...] = (
    _a("first_light", "First steps", 1, "First light", "Looked through a first quarter of a square", count("quarters", 1)),
    _a("first_mark", "First steps", 2, "First mark", "Put a first mark on the sky", count("marks", 1)),
    _a("first_check", "First steps", 3, "First check", "Answered a first machine finding", count("answers", 1)),
    _a("named", "First steps", 4, "Named", "Chose a public name", flag("named")),
    _a("stargazer", "Squares looked through", 1, "Stargazer", "10 squares looked through", count("squares", 10)),
    _a("surveyor", "Squares looked through", 2, "Surveyor", "50 squares looked through", count("squares", 50)),
    _a("mapper", "Squares looked through", 3, "Mapper", "100 squares looked through", count("squares", 100)),
    _a("cartographer", "Squares looked through", 4, "Cartographer", "500 squares looked through", count("squares", 500)),
    _a("atlas", "Squares looked through", 5, "Atlas", "1000 squares looked through", count("squares", 1000)),
    _a("sky_keeper", "Squares looked through", 6, "Sky keeper", "5000 squares looked through", count("squares", 5000)),
    _a("sharp_eye", "Accuracy", 1, "Sharp eye", "Caught a known mover in a control square", count("control", 1)),
    _a("eagle_eye", "Accuracy", 2, "Eagle eye", "Caught 10 known movers in control squares", count("control", 10)),
    _a("never_fooled", "Accuracy", 3, "Never fooled", "20 right answers in a row", count("run", 20)),
    _a("nine_of_ten", "Accuracy", 4, "Nine of ten", "Accuracy of 90% or more over 100 answers", _nine_of_ten),
    _a("flawless_hundred", "Accuracy", 5, "Flawless hundred", "100 right answers in a row", count("run", 100)),
    _a(
        "catalogue_reader",
        "Knowing the catalogues",
        1,
        "Catalogue reader",
        "10 known objects rightly called known",
        count("known_right", 10),
    ),
    _a("librarian", "Knowing the catalogues", 2, "Librarian", "100 known objects rightly called known", count("known_right", 100)),
    _a("asteroid_wrangler", "Knowing the catalogues", 3, "Asteroid wrangler", "10 JPL bodies rightly called", count("jpl_right", 10)),
    _a("artefact_spotter", "Knowing the catalogues", 4, "Artefact spotter", "10 artefacts rightly called", count("artefact_right", 10)),
    _a("ghostbuster", "Knowing the catalogues", 5, "Ghostbuster", "100 artefacts rightly called", count("artefact_right", 100)),
    _a(
        "barnards_runner",
        "Famous movers",
        1,
        "Barnard's runner",
        "Caught Barnard's Star in a control square",
        lambda s: (1.0 if "Barnard's Star" in s.targets else 0.0, 1.0),
    ),
    _a(
        "cold_neighbour",
        "Famous movers",
        2,
        "Cold neighbour",
        "Caught ε Indi B or WISE 0855−0714 in a control square",
        lambda s: (1.0 if s.targets & {"ε Indi B", "WISE 0855−0714"} else 0.0, 1.0),
    ),
    _a(
        "far_world",
        "Famous movers",
        3,
        "Far world",
        "Looked through the square of Eris, Makemake or Sedna and marked it",
        flag("far_world"),
    ),
    _a("p9_band", "Places in the sky", 1, "In the Planet Nine band", "25 squares in the predicted Planet Nine band", count("p9", 25)),
    _a("p9_deep", "Places in the sky", 2, "Deep in the band", "250 squares in the predicted Planet Nine band", count("p9", 250)),
    _a("ecliptic_walker", "Places in the sky", 3, "Ecliptic walker", "100 squares on the ecliptic", count("ecliptic", 100)),
    _a(
        "both_skies",
        "Places in the sky",
        4,
        "Both skies",
        "Squares in the north and in the south",
        lambda s: (float(s.north) + float(s.south), 2.0),
    ),
    _a("polar_explorer", "Places in the sky", 5, "Polar explorer", "A square beyond ±80° of declination", flag("polar")),
    _a("milky_way", "Places in the sky", 6, "Through the Milky Way", "10 squares in the galactic plane", count("galactic", 10)),
    _a(
        "around_the_clock",
        "Places in the sky",
        7,
        "Around the clock",
        "A square in each of the 24 hours of right ascension",
        lambda s: (float(len(s.hours)), 24.0),
    ),
    _a("pioneer", "First eyes", 1, "Pioneer", "10 squares nobody had viewed", count("first_eyes", 10)),
    _a("trailblazer", "First eyes", 2, "Trailblazer", "100 squares nobody had viewed", count("first_eyes", 100)),
    _a("frontier", "First eyes", 3, "Frontier", "1000 squares nobody had viewed", count("first_eyes", 1000)),
    _a("note_taker", "Investigation", 1, "Note taker", "A first investigation with notes", flag("inv_notes")),
    _a("measurer", "Investigation", 2, "Measurer", "Measured a motion with the ruler within 20% of the catalogue value", flag("measurer")),
    _a("time_traveller", "Investigation", 3, "Time traveller", "Found the same object in an old survey", flag("time_traveller")),
    _a("case_closed", "Investigation", 4, "Case closed", "10 investigations finished with a verdict", count("closed", 10)),
    _a("first_submit", "Submitting", 1, "First submit", "A first finding that passed every check", count("submits", 1)),
    _a("clean_sheet", "Submitting", 2, "Clean sheet", "A submit that passed every check with no warning", flag("clean")),
    _a("patient", "Submitting", 3, "Patient", "A submit that waited for the whole NEOWISE check", flag("patient")),
    _a("five_passed", "Submitting", 4, "Five that passed", "Five findings that passed every check", count("submits", 5)),
    _a("candidate", "Verdicts", 1, "Candidate", "A finding that became a candidate", count("candidates", 1)),
    _a("three_candidates", "Verdicts", 2, "Three candidates", "Three findings that became candidates", count("candidates", 3)),
    _a("confirmed", "Verdicts", 3, "Confirmed discovery", "A finding that was confirmed", count("confirmed", 1)),
    _a("peer", "Second look", 1, "Peer", "10 right answers on findings submitted by other people", count("peer_right", 10)),
    _a("referee", "Second look", 2, "Referee", "100 right answers on findings submitted by other people", count("peer_right", 100)),
    _a("days_3", "Habit", 1, "Three days in a row", "Searched on three days in a row", count("days_run", 3)),
    _a("days_7", "Habit", 2, "Seven days in a row", "Searched on seven days in a row", count("days_run", 7)),
    _a("days_30", "Habit", 3, "Thirty days in a row", "Searched on thirty days in a row", count("days_run", 30)),
    _a("days_100", "Habit", 4, "Hundred days in a row", "Searched on a hundred days in a row", count("days_run", 100)),
    _a("weekend_watch", "Habit", 5, "Weekend watch", "Searched on both days of four weekends", count("weekends", 4)),
    _a("top_ten_week", "Leaderboard", 1, "Top ten of a week", "In the top ten of a finished week", flag("top_ten_week")),
    _a("first_week", "Leaderboard", 2, "First of a week", "First place of a finished week", flag("first_week")),
    _a("first_month", "Leaderboard", 3, "First of a month", "First place of a finished month", flag("first_month")),
    _a("tombaugh", "Hidden", 1, "Tombaugh", "Looked through the square where Pluto was found in 1930", flag("tombaugh"), hidden=True),
    _a("night_owl", "Hidden", 2, "Night owl", "A real look between 00:00 and 04:00 local time", flag("night_owl"), hidden=True),
    _a(
        "second_opinion",
        "Hidden",
        3,
        "Second opinion",
        "Rightly called “artefact” a finding the machine ranked in its top ten",
        flag("second_opinion"),
        hidden=True,
    ),
)
BY_ID = {a.id: a for a in ACHIEVEMENTS}

# ------------------------------------------------------------------------------------------------------------------ the score


def level_of(points: int) -> tuple[str, int | None]:
    """(the level's name, the points of the next level or None at the top)."""
    name, nxt = LEVELS[0][1], None
    for i, (at, n) in enumerate(LEVELS):
        if points >= at:
            name, nxt = n, (LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None)
    return name, nxt


def period_start(period: str, now: float | None = None) -> float:
    """The start of today, of this week (Monday), of this month (all 00:00 UTC), or of everything."""
    t = datetime.fromtimestamp(now or time.time(), UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return t.timestamp()
    if period == "week":
        return (t - timedelta(days=t.weekday())).timestamp()
    if period == "month":
        return t.replace(day=1).timestamp()
    return 0.0


class Score:
    def __init__(self, st: Any, db: AccountsDB) -> None:
        self.st = st
        self.db = db
        c = db._conn()
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(acc_users)")}
        for col, kind in (("hide_board", "INTEGER NOT NULL DEFAULT 0"), ("named", "INTEGER NOT NULL DEFAULT 0")):
            if col not in cols:
                c.execute(f"ALTER TABLE acc_users ADD COLUMN {col} {kind}")
        cols = {r[1] for r in c.execute("PRAGMA table_info(acc_investigations)")}
        if "extra" not in cols:  # what the lab adds for the achievements: the ruler's result, the old-survey match
            c.execute("ALTER TABLE acc_investigations ADD COLUMN extra TEXT NOT NULL DEFAULT '{}'")
        self.controls = control_movers()

    # ----------------------------------------------------------------------------------------------------------------- events

    def _event(
        self, user_id: str, kind: str, ref: str, points: int, right: bool | None, detail: dict[str, Any], at: float | None = None
    ) -> bool:
        """One count per user per thing: the first record stays. True when it was new."""
        cur = self.db._conn().execute(
            "INSERT OR IGNORE INTO acc_score_events(user_id,kind,ref,points,right,at,detail) VALUES(?,?,?,?,?,?,?)",
            (user_id, kind, ref, points, None if right is None else int(right), at or time.time(), json.dumps(detail, ensure_ascii=False)),
        )
        return cur.rowcount == 1

    def events(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db._conn().execute("SELECT * FROM acc_score_events WHERE user_id=? ORDER BY at, id", (user_id,))
        return [
            {"kind": r["kind"], "ref": r["ref"], "points": r["points"], "right": None if r["right"] is None else bool(r["right"]),
             "at": r["at"], "detail": json.loads(r["detail"])}
            for r in rows
        ]  # fmt: skip

    # ------------------------------------------------------------------------------------------------------------ what happens

    def mark_put(self, user_id: str) -> None:
        self._event(user_id, "mark", "first", 0, None, {})

    def renamed(self, user_id: str) -> None:
        self.db._conn().execute("UPDATE acc_users SET named=1 WHERE id=?", (user_id,))

    def control_in(self, ra: float | None, dec: float | None, size: float = CELL_DEG) -> dict[str, Any] | None:
        if ra is None or dec is None:
            return None
        return next((m for m in self.controls if in_square(ra, dec, (m["ra"], m["dec"]), size)), None)

    def square_done(self, user_id: str, task: dict[str, Any], look: dict[str, Any], marks: list[dict[str, Any]]) -> int:
        """A task "Look through a square" was closed: a quarter (10′) of it, or the whole square for a task of before the quarters.
        `look` is what the lab says about the look: seconds, passes_shown, passes_total, local_hour. 5 points a quarter (a whole
        square is worth 20), with the 60 s / every-pass rule applied to each look. Returns the points paid now."""
        cell = str(task.get("cell_id") or "")
        if not cell:
            return 0
        q = task.get("quarter") or ""
        seconds, shown, total = look.get("seconds"), look.get("passes_shown"), look.get("passes_total")
        real = seconds is not None and seconds >= MIN_LOOK_S and shown is not None and total is not None and total >= 1 and shown >= total
        if not real:
            return 0  # not a real look: nothing is counted, right or wrong
        c = self.db._conn()
        paid = 0
        size = QUARTER_DEG if q else CELL_DEG
        facts = place_facts(task.get("ra"), task.get("dec"), size)
        day0 = period_start("day")
        today = c.execute(
            "SELECT COALESCE(SUM(CASE WHEN ref LIKE '%:%' THEN 1 ELSE 4 END),0) FROM acc_score_events"
            " WHERE user_id=? AND kind='square' AND points>0 AND at>=?",
            (user_id, day0),
        ).fetchone()[0]  # in quarters: a whole square of before counts as four
        try:
            others = c.execute(
                "SELECT COUNT(DISTINCT viewer) FROM grid_views WHERE cell_id=? AND viewer LIKE 'user:%' AND viewer!=?"
                + (" AND COALESCE(quarter,'') IN ('', ?)" if q else ""),
                (cell, f"user:{user_id}", *([q] if q else [])),
            ).fetchone()[0]
        except Exception:  # the grid stream's table (or its quarter column) may not exist: nobody is known to have viewed it
            others = 0
        hour = look.get("local_hour")
        detail = {**facts, "cell": cell, "quarter": q or None, "first": others == 0, "marked": task.get("result") == "marks", "local_hour": hour,
                  "seconds": seconds}  # fmt: skip
        worth = POINTS["square"] if q else POINTS["square"] * 4
        points = worth if today + (1 if q else 4) <= DAILY_SQUARES * 4 else 0
        if self._event(user_id, "square", f"{cell}:{q}" if q else cell, points, None, detail):
            paid += points
        mover = self.control_in(task.get("ra"), task.get("dec"), size)
        if mover is not None:  # a control quarter: was the known mover marked without being told?
            near = [m for m in marks if sep_arcsec(m["ra"], m["dec"], mover["ra"], mover["dec"]) <= CONTROL_RADIUS_ARCSEC]
            caught = bool(near)
            if self._event(user_id, "control", cell, POINTS["control"] if caught else 0, caught, {"target": mover["name"]}):
                paid += POINTS["control"] if caught else 0
        return paid

    def finding_facts(self, finding_id: str) -> dict[str, Any]:
        """What is known about a finding now: its stage when final, its kind, whether JPL names it, its rank on the page."""
        source = getattr(self.st, "score_findings", None)  # a test may put its own table here
        if callable(source):
            return dict(source().get(finding_id) or {})
        f = getattr(self.st, "findings", None)
        if f is None or finding_id.startswith("user:"):
            return {}
        from service.findings import query

        names = getattr(getattr(self.st, "fpx_known", None), "verdict_on", None)
        finds = query(f.db, None, None, None, 100000, 0, names=names, slim=True)["finds"]
        for rank, x in enumerate(finds):
            if x["id"] == finding_id:
                one = query(f.db, None, "any", None, 1, 0, only=frozenset({finding_id}))["finds"]
                jpl = any(
                    i.get("state") == "ok" and str(i.get("catalogue")) in ("jpl", "sbident", "horizons")
                    for i in (one[0].get("idents") if one else None) or []
                )
                return {"kind": x["kind"], "status": x["status"], "stage": STAGE_OF_STATUS.get(x["status"]), "jpl": jpl, "rank": rank + 1}
        return {}

    def stage_of(self, finding_id: str, facts: dict[str, Any] | None = None) -> str | None:
        """The final stage of a finding, or None while it has none: the verdict first, then the catalogues' own answer."""
        c = self.db._conn()
        r = c.execute("SELECT status FROM acc_verdicts WHERE finding_id=?", (finding_id,)).fetchone()
        if r is not None:
            return r["status"] if r["status"] in FINAL_STAGES else None
        r = c.execute("SELECT status FROM acc_submissions WHERE finding_id=?", (finding_id,)).fetchone()
        if r is not None and r["status"] in FINAL_STAGES:
            return str(r["status"])
        stage = (facts if facts is not None else self.finding_facts(finding_id)).get("stage")
        return stage if stage in FINAL_STAGES else None

    def answered(self, user_id: str, finding_id: str, answer: str) -> int:
        """The task "Check a machine finding" was closed with an answer. Paid when (and if) the stage is final and agrees."""
        if answer not in AGREES or not finding_id:
            return 0
        facts = self.finding_facts(finding_id)
        c = self.db._conn()
        other = c.execute("SELECT user_id FROM acc_submissions WHERE finding_id=?", (finding_id,)).fetchone()
        detail = {
            "kind": facts.get("kind"),
            "jpl": bool(facts.get("jpl")),
            "rank": facts.get("rank"),
            "peer": other is not None and other["user_id"] != user_id,
        }
        c.execute(
            "INSERT OR IGNORE INTO acc_answers(user_id,finding_id,answer,at,detail) VALUES(?,?,?,?,?)",
            (user_id, finding_id, answer, time.time(), json.dumps(detail)),
        )  # the first answer counts: it cannot be changed after the stage is known
        return self.settle(user_id)

    def settle(self, user_id: str | None = None, finding_id: str | None = None) -> int:
        """Pay the waiting answers whose finding now has a final stage. Returns the points paid to `user_id` (0 without one)."""
        c = self.db._conn()
        q = "SELECT * FROM acc_answers WHERE settled IS NULL"
        args: list[str] = []
        if user_id:
            q, args = q + " AND user_id=?", [*args, user_id]
        if finding_id:
            q, args = q + " AND finding_id=?", [*args, finding_id]
        paid = 0
        cache: dict[str, str | None] = {}
        for r in c.execute(q, args).fetchall():
            fid = r["finding_id"]
            if fid not in cache:
                cache[fid] = self.stage_of(fid)
            stage = cache[fid]
            if stage is None:
                continue
            right = stage in AGREES[r["answer"]]
            detail = {**json.loads(r["detail"]), "answer": r["answer"], "stage": stage, "answered_at": r["at"]}
            self._event(r["user_id"], "check", fid, POINTS["check"] if right else 0, right, detail)
            c.execute("UPDATE acc_answers SET settled=? WHERE user_id=? AND finding_id=?", (time.time(), r["user_id"], fid))
            if right and r["user_id"] == user_id:
                paid += POINTS["check"]
        return paid

    def submitted(self, user_id: str, finding_id: str, checks: list[dict[str, Any]]) -> None:
        """The server submitted a finding of this user: it passed every check."""
        warns = sum(1 for x in checks if x.get("state") == "warn")
        neo = next((x for x in checks if x.get("id") == "neowise"), None)
        whole = neo is not None and neo.get("state") in ("pass", "warn") and "not applicable" not in str(neo.get("detail") or "").lower()
        self._event(user_id, "submit", finding_id, POINTS["submit"], None, {"warns": warns, "neowise": bool(whole)})

    def verdict(self, finding_id: str, status: str) -> None:
        """The verdict tool gave a finding its stage: the submitter of a candidate or a confirmed finding is paid, and every
        answer that waited for this finding is settled."""
        c = self.db._conn()
        c.execute(
            "INSERT INTO acc_verdicts(finding_id,status,at) VALUES(?,?,?) ON CONFLICT(finding_id) DO UPDATE SET status=excluded.status, at=excluded.at",
            (finding_id, status, time.time()),
        )
        who = c.execute("SELECT user_id FROM acc_submissions WHERE finding_id=? AND user_id!=''", (finding_id,)).fetchone()
        if who is not None and status in ("candidate", "confirmed"):
            self._event(who["user_id"], status, finding_id, POINTS[status], None, {})
            if status == "confirmed":  # a confirmed finding was a candidate on the way
                self._event(who["user_id"], "candidate", finding_id, POINTS["candidate"], None, {})
        self.settle(finding_id=finding_id)
        from service.accounts import agreement  # the "Does it really move?" answers wait for the same stage

        agreement.Agreement(self).settle_moves(finding_id=finding_id)
        for r in c.execute("SELECT DISTINCT user_id FROM acc_score_events WHERE ref=?", (finding_id,)).fetchall():
            self.award(r["user_id"])

    # ------------------------------------------------------------------------------------------------------------------ stats

    def stats(self, user_id: str, events: list[dict[str, Any]] | None = None) -> Stats:
        c = self.db._conn()
        s = Stats()
        ev = events if events is not None else self.events(user_id)
        u = c.execute("SELECT named FROM acc_users WHERE id=?", (user_id,)).fetchone()
        s.named = bool(u and u["named"])
        s.answers = c.execute("SELECT COUNT(*) FROM acc_answers WHERE user_id=?", (user_id,)).fetchone()[0]
        days: set[int] = {int(r[0] // 86400) for r in c.execute("SELECT at FROM acc_answers WHERE user_id=?", (user_id,))}
        run = 0
        judged = sorted((e for e in ev if e["right"] is not None), key=lambda e: e["detail"].get("answered_at") or e["at"])
        for e in judged:
            s.settled += 1
            run = run + 1 if e["right"] else 0
            s.run = max(s.run, run)
            s.right += int(e["right"])
        quarters_of: dict[str, set[str]] = {}
        places: dict[str, set[str]] = {}
        for e in ev:
            d, kind = e["detail"], e["kind"]
            if kind == "mark":
                s.marks = 1
            elif kind == "square":
                quarters_of.setdefault(str(d.get("cell") or e["ref"].split(":")[0]), set()).update(
                    {d["quarter"]} if d.get("quarter") else {"NW", "NE", "SW", "SE"}
                )
                s.quarters += 1
                days.add(int(e["at"] // 86400))
                sq = str(d.get("cell") or e["ref"].split(":")[0])
                for flag in ("p9", "ecliptic", "galactic"):  # the place families count squares, not quarters
                    if d.get(flag):
                        places.setdefault(flag, set()).add(sq)
                s.polar = s.polar or bool(d.get("polar"))
                if d.get("dec") is not None:
                    s.north, s.south = s.north or d["dec"] > 0, s.south or d["dec"] < 0
                if d.get("hour") is not None:
                    s.hours.add(int(d["hour"]))
                if d.get("first"):
                    places.setdefault("first", set()).add(sq)
                s.tombaugh = s.tombaugh or bool(d.get("tombaugh"))
                s.far_world = s.far_world or bool(d.get("far_world") and d.get("marked"))
                h = d.get("local_hour")
                s.night_owl = s.night_owl or (h is not None and NIGHT_HOURS[0] <= int(h) < NIGHT_HOURS[1])
            elif kind == "control" and e["right"]:
                s.control += 1
                s.targets.add(str(d.get("target")))
            elif kind == "check" and e["right"]:
                stage, answer = d.get("stage"), d.get("answer")
                s.known_right += int(stage == "known" and not d.get("jpl"))
                s.jpl_right += int(stage == "known" and bool(d.get("jpl")))
                s.artefact_right += int(stage == "artefact")
                s.peer_right += int(bool(d.get("peer")))
                s.second_opinion = s.second_opinion or (answer == "artefact" and (d.get("rank") or 99) <= 10)
            elif kind == "submit":
                s.submits += 1
                days.add(int(e["at"] // 86400))
                s.clean = s.clean or d.get("warns") == 0
                s.patient = s.patient or bool(d.get("neowise"))
            elif kind == "candidate":
                s.candidates += 1
            elif kind == "confirmed":
                s.confirmed += 1
        s.squares = sum(1 for qs in quarters_of.values() if len(qs) >= 4)  # whole squares: all four quarters
        s.p9, s.ecliptic, s.galactic = (len(places.get(k, ())) for k in ("p9", "ecliptic", "galactic"))
        s.first_eyes = len(places.get("first", ()))
        for r in c.execute("SELECT notes, extra, submitted FROM acc_investigations WHERE user_id=?", (user_id,)):
            s.inv_notes = s.inv_notes or bool(json.loads(r["notes"]))
            extra = json.loads(r["extra"] or "{}")
            for ruler in [extra.get("ruler") or {}, *[x.get("ruler") or {} for x in extra.get("items", [])]]:
                try:  # the ruler's result of the investigation, or of any object gathered in it
                    rate, cat = float(ruler["rate"]), float(ruler["catalogue_rate"])
                    s.measurer = s.measurer or (cat > 0 and abs(rate - cat) / cat <= 0.2)
                except (KeyError, TypeError, ValueError):
                    pass
            s.time_traveller = s.time_traveller or bool(extra.get("old_survey"))
            s.closed += int(bool(r["submitted"]) and self.stage_of(r["submitted"], {}) is not None)
        # days in a row, and weekends with both days
        best = cur = 0
        for d in sorted(days):
            cur = cur + 1 if d - 1 in days else 1
            best = max(best, cur)
        s.days_run = best
        s.weekends = sum(1 for d in days if d % 7 == 2 and d + 1 in days)  # day 2 of the epoch count is a Saturday (1 Jan 1970: Thursday)
        got = {r[0] for r in c.execute("SELECT id FROM acc_achievements WHERE user_id=?", (user_id,))}
        s.top_ten_week, s.first_week, s.first_month = "top_ten_week" in got, "first_week" in got, "first_month" in got
        return s

    # ------------------------------------------------------------------------------------------------------------ achievements

    def _board_awards(self, user_id: str, s: Stats) -> None:
        """The leaderboard achievements look at the last finished week and month."""
        now = time.time()
        week0, month0 = period_start("week", now), period_start("month", now)
        last_week = self.board(week0 - 7 * 86400, week0)
        prev_month_start = period_start("month", month0 - 86400)
        last_month = self.board(prev_month_start, month0)
        place = next((i + 1 for i, r in enumerate(last_week) if r["user_id"] == user_id), None)
        s.top_ten_week = s.top_ten_week or (place is not None and place <= 10)
        s.first_week = s.first_week or place == 1
        s.first_month = s.first_month or bool(last_month and last_month[0]["user_id"] == user_id)

    def award(self, user_id: str) -> list[dict[str, Any]]:
        """Record and announce the achievements the user has earned since the last look; returns the new ones."""
        c = self.db._conn()
        s = self.stats(user_id)
        self._board_awards(user_id, s)
        got = {r[0] for r in c.execute("SELECT id FROM acc_achievements WHERE user_id=?", (user_id,))}
        new = []
        for a in ACHIEVEMENTS:
            have, need = a.rule(s)
            if a.id not in got and have >= need:
                c.execute("INSERT OR IGNORE INTO acc_achievements(user_id,id,at) VALUES(?,?,?)", (user_id, a.id, time.time()))
                self.db.notify(user_id, "achievement", f"Achievement: {a.name}. {a.line}.", "#/me?tab=score")
                new.append({"id": a.id, "name": a.name, "line": a.line})
        return new

    def rarity(self) -> tuple[dict[str, int], int]:
        """({achievement id: how many have it}, how many searchers there are): a searcher is a user with at least one event."""
        c = self.db._conn()
        total = c.execute("SELECT COUNT(DISTINCT user_id) FROM acc_score_events").fetchone()[0]
        return {r[0]: int(r[1]) for r in c.execute("SELECT id, COUNT(*) FROM acc_achievements GROUP BY id")}, int(total)

    def achievements(self, user_id: str, s: Stats) -> list[dict[str, Any]]:
        c = self.db._conn()
        got = {r["id"]: r["at"] for r in c.execute("SELECT id, at FROM acc_achievements WHERE user_id=?", (user_id,))}
        have_it, total = self.rarity()
        out = []
        for a in ACHIEVEMENTS:
            have, need = a.rule(s)
            earned = a.id in got
            secret = a.hidden and not earned
            out.append(
                {
                    "id": a.id, "family": a.family, "tier": a.tier,
                    "name": "?" if secret else a.name, "line": "A hidden achievement" if secret else a.line, "hidden": a.hidden,
                    "earned_at": iso(got[a.id]) if earned else None,
                    "progress": 1.0 if earned else (0.0 if secret else round(min(1.0, have / need), 3)),
                    "have": None if secret else int(min(have, need)), "need": None if secret else int(need),
                    "rarity": None if total < RARITY_MIN_SEARCHERS else round(100.0 * have_it.get(a.id, 0) / total, 1),
                }
            )  # fmt: skip
        return out

    # ------------------------------------------------------------------------------------------------------------- the answers

    def summary(self, user_id: str) -> dict[str, Any]:
        self.settle(user_id)
        self.award(user_id)
        ev = self.events(user_id)
        s = self.stats(user_id, ev)
        points = sum(e["points"] for e in ev)
        level, nxt = level_of(points)
        waiting = self.db._conn().execute("SELECT COUNT(*) FROM acc_answers WHERE user_id=? AND settled IS NULL", (user_id,)).fetchone()[0]
        hide = self.db._conn().execute("SELECT hide_board FROM acc_users WHERE id=?", (user_id,)).fetchone()
        shown = [
            {"kind": e["kind"], "ref": e["ref"], "points": e["points"], "right": e["right"], "at": iso(e["at"]),
             "target": e["detail"].get("target"), "answer": e["detail"].get("answer"), "stage": e["detail"].get("stage"), "result": e["detail"].get("result")}
            for e in reversed(ev)
            if e["kind"] != "mark"
        ]  # fmt: skip
        return {
            "points": points, "level": level, "next_level_at": nxt, "levels": [{"at": at, "name": n} for at, n in LEVELS],
            "accuracy": None if s.accuracy is None else round(s.accuracy, 3), "right": s.right, "answered": s.settled, "waiting": int(waiting),
            "squares": s.squares, "quarters": s.quarters, "hide_from_board": bool(hide and hide["hide_board"]),
            "events": shown[:500], "achievements": self.achievements(user_id, s),
            "rules": {"points": POINTS, "min_look_s": MIN_LOOK_S, "daily_squares": DAILY_SQUARES, "quarter_arcmin": 10, "rank_min_answers": RANK_MIN_ANSWERS},
        }  # fmt: skip

    def board(self, start: float, end: float | None = None) -> list[dict[str, Any]]:
        """Everyone with points in the period, the most points first (ties: the earlier to get there). Hidden users and users
        with fewer than RANK_MIN_ANSWERS settled answers are not ranked."""
        c = self.db._conn()
        end = end or time.time() + 1
        rows = c.execute(
            "SELECT e.user_id, u.name, u.hide_board, SUM(e.points) points, SUM(CASE WHEN e.kind='square' THEN (CASE WHEN e.ref LIKE '%:%'"
            " THEN 0.25 ELSE 1 END) ELSE 0 END) squares, SUM(e.kind='confirmed') confirmed,"
            " MAX(e.at) last FROM acc_score_events e JOIN acc_users u ON u.id=e.user_id WHERE e.at>=? AND e.at<? GROUP BY e.user_id",
            (start, end),
        ).fetchall()
        judged = {
            r[0]: (int(r[1]), int(r[2] or 0))
            for r in c.execute("SELECT user_id, COUNT(right), SUM(right) FROM acc_score_events WHERE right IS NOT NULL GROUP BY user_id")
        }
        out = []
        for r in rows:
            n, right = judged.get(r["user_id"], (0, 0))
            out.append(
                {
                    "user_id": r["user_id"], "name": r["name"], "points": int(r["points"] or 0), "squares": int(r["squares"] or 0),  # squares' worth
                    "confirmed": int(r["confirmed"] or 0), "accuracy": None if n == 0 else round(right / n, 3), "answered": n,
                    "ranked": n >= RANK_MIN_ANSWERS and not r["hide_board"] and int(r["points"] or 0) > 0, "last": r["last"],
                }
            )  # fmt: skip
        ranked = sorted((x for x in out if x["ranked"]), key=lambda x: (-x["points"], x["last"]))
        return ranked + sorted((x for x in out if not x["ranked"]), key=lambda x: (-x["points"], x["last"]))

    def leaders(self, period: str, me: str | None, limit: int = 100) -> dict[str, Any]:
        board = self.board(period_start(period))
        c = self.db._conn()
        have_it, _ = self.rarity()

        def badges(user_id: str) -> list[dict[str, str]]:
            """The three rarest achievements of the user."""
            mine = [BY_ID[r[0]] for r in c.execute("SELECT id FROM acc_achievements WHERE user_id=?", (user_id,)) if r[0] in BY_ID]
            mine.sort(key=lambda a: (have_it.get(a.id, 0), -a.tier))
            return [{"id": a.id, "name": a.name} for a in mine[:3]]

        rows, place = [], 0
        for x in board:
            if x["ranked"]:
                place += 1
            if (x["ranked"] and place <= limit) or x["user_id"] == me:
                rows.append(
                    {
                        "place": place if x["ranked"] else None, "name": x["name"], "points": x["points"], "squares": x["squares"],
                        "confirmed": x["confirmed"], "accuracy": x["accuracy"], "me": x["user_id"] == me, "badges": badges(x["user_id"]),
                        "answers_to_rank": max(0, RANK_MIN_ANSWERS - x["answered"]) if x["user_id"] == me and not x["ranked"] else None,
                    }
                )  # fmt: skip
        if me and not any(r["me"] for r in rows):  # the signed-in user's own row is always shown, even with no points in the period
            u = self.db.user(me)
            if u is not None:
                rows.append({"place": None, "name": u["name"], "points": 0, "squares": 0, "confirmed": 0, "accuracy": None, "me": True,
                             "badges": badges(me), "answers_to_rank": None})  # fmt: skip
        return {
            "period": period,
            "since": iso(period_start(period)) if period != "all" else None,
            "rank_min_answers": RANK_MIN_ANSWERS,
            "rows": rows,
        }


def get(st: Any, db: AccountsDB) -> Score:
    s: Score | None = getattr(st, "score", None)
    if s is None or s.db is not db:
        s = st.score = Score(st, db)
    return s
