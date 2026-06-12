"""Pure tournament logic — a faithful re-implementation of the Excel workbook.

Every function here mirrors a formula (or block of formulas) in
``data/World_Cup_sweepstake.xlsx`` (sheet *Tracker*).  The module has **no**
framework or I/O dependencies so the behaviour can be unit-tested in isolation
against the spreadsheet's logic.

Mapping to the workbook
-----------------------
* ``match_played``       -> column ``Played`` ``=IF(AND(Home<>"",Away<>""),1,"")``
* ``side_points``        -> columns ``Home Pts`` / ``Away Pts`` (3 / 1 / 0)
* ``match_result``       -> column ``Result`` ("H" / "D" / "A")
* ``group_standings``    -> the ``GroupStandings`` table (BB:BL), COUNTIFS/SUMIFS
* ``_rank_key``          -> column ``Rank`` (Pts, GD, GF, name) per BL5 formula
* ``best_third_placed``  -> ``=TAKE(SORTBY(FILTER(... Rank=3 ...)),8)`` (A80)
* ``resolve_winner``     -> column ``Winner`` ``=IF(override,...,IF(score1>score2,...))``
* ``BRACKET``            -> the C/F seeding array-formulas of the knockout rows
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Optional


def _fold(name: str) -> str:
    """Accent-insensitive, case-insensitive sort key.

    Excel's text comparison (``GroupStandings[Team],"<"``) is locale-aware: it
    sorts "Côte d'Ivoire" as if spelled "Cote" (before "Curaçao") and ignores
    case.  Python's default string order compares raw code points instead
    (``ô`` > ``u``), which would flip such ties.  We strip diacritics and
    case-fold so the final name tie-break matches the spreadsheet exactly.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


# --------------------------------------------------------------------------- #
#  Group-stage primitives (one row of the GroupFixtures table)
# --------------------------------------------------------------------------- #
def match_played(home_score, away_score) -> bool:
    """Played when *both* scores are entered (Excel ``Played`` column)."""
    return home_score is not None and away_score is not None


def side_points(goals_for: int, goals_against: int) -> int:
    """3 for a win, 1 for a draw, 0 for a loss (``Home Pts`` / ``Away Pts``)."""
    if goals_for > goals_against:
        return 3
    if goals_for == goals_against:
        return 1
    return 0


def match_result(home_score, away_score) -> Optional[str]:
    """'H' home win, 'D' draw, 'A' away win, or ``None`` if not played."""
    if not match_played(home_score, away_score):
        return None
    if home_score > away_score:
        return "H"
    if home_score == away_score:
        return "D"
    return "A"


# --------------------------------------------------------------------------- #
#  Standings row
# --------------------------------------------------------------------------- #
@dataclass
class TeamRow:
    country: str
    group: str
    owner: str = ""
    owner_short: str = ""
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gd"] = self.gd
        d["points"] = self.points
        return d


def _rank_key(row: TeamRow):
    """Sort key matching the Excel ``Rank`` formula (BL column).

    Excel ranks within a group by: Points desc, Goal Difference desc,
    Goals For desc, then Team name ascending as the final deterministic
    tie-break (``GroupStandings[Team],"<"``).  We negate the numeric fields so a
    plain ascending sort yields best-first.
    """
    return (-row.points, -row.gd, -row.gf, _fold(row.country))


def group_standings(teams: list[dict], results: dict) -> dict[str, list[TeamRow]]:
    """Build ranked standings per group.

    ``teams``   : list of {country, group, owner, owner_short}
    ``results`` : {group_match_id: {"home": int, "away": int}} e.g. {"A1": {...}}

    Returns ``{group: [TeamRow, ...]}`` ordered best-first (rank 1 first), which
    reproduces the aggregation in the ``GroupStandings`` helper table together
    with its ranking.
    """
    rows: dict[str, TeamRow] = {}
    by_group: dict[str, list[TeamRow]] = {}
    for t in teams:
        row = TeamRow(
            country=t["country"], group=t["group"],
            owner=t.get("owner", ""), owner_short=t.get("owner_short", ""),
        )
        rows[t["country"]] = row
        by_group.setdefault(t["group"], []).append(row)

    for fx in _GROUP_FIXTURES:
        res = results.get(fx["match"])
        if not res:
            continue
        hs, as_ = res.get("home"), res.get("away")
        if not match_played(hs, as_):
            continue
        home, away = rows.get(fx["home"]), rows.get(fx["away"])
        if home is None or away is None:
            continue
        home.played += 1
        away.played += 1
        home.gf += hs
        home.ga += as_
        away.gf += as_
        away.ga += hs
        if hs > as_:
            home.won += 1
            away.lost += 1
        elif hs < as_:
            away.won += 1
            home.lost += 1
        else:
            home.drawn += 1
            away.drawn += 1

    for group in by_group:
        by_group[group].sort(key=_rank_key)
    return by_group


def group_winner(standings: dict[str, list[TeamRow]], group: str, rank: int) -> Optional[str]:
    """The team finishing ``rank`` (1-based) in ``group``; mirrors the R32
    seeding array-formula ``XLOOKUP(1,(Group=g)*(Rank=r),Team)``."""
    table = standings.get(group, [])
    if 1 <= rank <= len(table):
        return table[rank - 1].country
    return None


# --------------------------------------------------------------------------- #
#  Best third-placed teams  (Excel A80 TAKE/SORTBY/FILTER over Rank=3)
# --------------------------------------------------------------------------- #
def best_third_placed(standings: dict[str, list[TeamRow]], take: int = 8) -> list[TeamRow]:
    """Return the best ``take`` third-placed teams across all groups.

    Mirrors ``=TAKE(SORTBY(FILTER(... Rank=3 ...), Pts, GD, ..., Team), 8)``.
    The third-placed team of every group is collected then ordered by
    Points desc, GD desc, GF desc, Team name asc (the same comparator used for
    in-group ranking — see ``_rank_key``).  The top ``take`` qualify.
    """
    thirds = [table[2] for table in standings.values() if len(table) >= 3]
    thirds.sort(key=_rank_key)
    return thirds[:take]


# --------------------------------------------------------------------------- #
#  Knockout bracket structure (derived from the Tracker C/F/H formulas)
# --------------------------------------------------------------------------- #
# A seed is one of:
#   ("group", "A", 1)  -> winner / runner-up of a group
#   ("third", "D")     -> third-placed team allocated from group D
#   ("winner", 74)     -> winner of match 74
#   ("loser", 101)     -> loser of match 101  (used for the third-place play-off)
BRACKET: dict[int, dict] = {
    # ---- Round of 32 (matches 73-88) -------------------------------------- #
    73: {"round": "Round of 32", "a": ("group", "A", 2), "b": ("group", "B", 2)},
    74: {"round": "Round of 32", "a": ("group", "E", 1), "b": ("third", "D"),
         "pool": "A/B/C/D/F"},
    75: {"round": "Round of 32", "a": ("group", "F", 1), "b": ("group", "C", 2)},
    76: {"round": "Round of 32", "a": ("group", "C", 1), "b": ("group", "F", 2)},
    77: {"round": "Round of 32", "a": ("group", "I", 1), "b": ("third", "F"),
         "pool": "C/D/F/G/H"},
    78: {"round": "Round of 32", "a": ("group", "E", 2), "b": ("group", "I", 2)},
    79: {"round": "Round of 32", "a": ("group", "A", 1), "b": ("third", "C"),
         "pool": "C/E/F/H/I"},
    80: {"round": "Round of 32", "a": ("group", "L", 1), "b": ("third", "K"),
         "pool": "E/H/I/J/K"},
    81: {"round": "Round of 32", "a": ("group", "D", 1), "b": ("third", "B"),
         "pool": "B/E/F/I/J"},
    82: {"round": "Round of 32", "a": ("group", "G", 1), "b": ("third", "A"),
         "pool": "A/E/H/I/J"},
    83: {"round": "Round of 32", "a": ("group", "K", 2), "b": ("group", "L", 2)},
    84: {"round": "Round of 32", "a": ("group", "H", 1), "b": ("group", "J", 2)},
    85: {"round": "Round of 32", "a": ("group", "B", 1), "b": ("third", "G"),
         "pool": "E/F/G/I/J"},
    86: {"round": "Round of 32", "a": ("group", "J", 1), "b": ("group", "H", 2)},
    87: {"round": "Round of 32", "a": ("group", "K", 1), "b": ("third", "L"),
         "pool": "D/E/I/J/L"},
    88: {"round": "Round of 32", "a": ("group", "D", 2), "b": ("group", "G", 2)},
    # ---- Round of 16 (matches 89-96) -------------------------------------- #
    89: {"round": "Round of 16", "a": ("winner", 74), "b": ("winner", 77)},
    90: {"round": "Round of 16", "a": ("winner", 73), "b": ("winner", 75)},
    91: {"round": "Round of 16", "a": ("winner", 76), "b": ("winner", 78)},
    92: {"round": "Round of 16", "a": ("winner", 79), "b": ("winner", 80)},
    93: {"round": "Round of 16", "a": ("winner", 83), "b": ("winner", 84)},
    94: {"round": "Round of 16", "a": ("winner", 81), "b": ("winner", 82)},
    95: {"round": "Round of 16", "a": ("winner", 86), "b": ("winner", 88)},
    96: {"round": "Round of 16", "a": ("winner", 85), "b": ("winner", 87)},
    # ---- Quarter-finals (matches 97-100) ---------------------------------- #
    97: {"round": "Quarter-final", "a": ("winner", 89), "b": ("winner", 90)},
    98: {"round": "Quarter-final", "a": ("winner", 93), "b": ("winner", 94)},
    99: {"round": "Quarter-final", "a": ("winner", 91), "b": ("winner", 92)},
    100: {"round": "Quarter-final", "a": ("winner", 95), "b": ("winner", 96)},
    # ---- Semi-finals (matches 101-102) ------------------------------------ #
    101: {"round": "Semi-final", "a": ("winner", 97), "b": ("winner", 98)},
    102: {"round": "Semi-final", "a": ("winner", 99), "b": ("winner", 100)},
    # ---- Third-place play-off (match 103, in the workbook) ---------------- #
    103: {"round": "Third Place", "a": ("loser", 101), "b": ("loser", 102)},
    # ---- Final (match 104) ------------------------------------------------ #
    # The workbook stops at the third-place play-off; the Final is added here so
    # the champion (and therefore the 1st/2nd sweepstake prizes) can be decided.
    104: {"round": "Final", "a": ("winner", 101), "b": ("winner", 102)},
}

# The eight Round-of-32 slots reserved for best-third-placed teams, mapped to the
# group whose third-placed team fills them.  This is the fixed FIFA allocation
# the workbook encodes for the qualifying-group combination A,B,C,D,F,G,K,L
# (see the note in the Tracker sheet).
THIRD_SLOT_GROUP = {74: "D", 77: "F", 79: "C", 80: "K", 81: "B", 82: "A", 85: "G", 87: "L"}


def assign_third_slots(thirds: list[TeamRow]) -> dict[int, Optional[str]]:
    """Map each third-place Round-of-32 slot to a qualified third-placed team.

    Primary pass uses the workbook's fixed group-letter allocation
    (:data:`THIRD_SLOT_GROUP`) — so for the spreadsheet's canonical qualifying
    combination (A,B,C,D,F,G,K,L) the result is identical to the workbook.

    If the eight qualifiers come from a *different* set of groups (the workbook
    would leave those ``XLOOKUP`` slots blank), the unmatched slots are filled
    with the remaining qualifiers in ranking order, so the bracket always
    completes for a live tournament.  This is the one place the site goes beyond
    the spreadsheet, and only ever for cases the spreadsheet left undefined.
    """
    by_group = {r.group: r.country for r in thirds}
    assigned: dict[int, Optional[str]] = {}
    used: set[str] = set()
    for slot in sorted(THIRD_SLOT_GROUP):
        team = by_group.get(THIRD_SLOT_GROUP[slot])
        if team:
            assigned[slot] = team
            used.add(team)
    leftover_slots = [s for s in sorted(THIRD_SLOT_GROUP) if s not in assigned]
    leftover_teams = [r.country for r in thirds if r.country not in used]
    for slot, team in zip(leftover_slots, leftover_teams):
        assigned[slot] = team
    return assigned


def resolve_winner(score1, score2, override: Optional[str],
                   team1: Optional[str], team2: Optional[str]) -> Optional[str]:
    """Replicates the ``Winner`` column.

    ``=IF(override<>"",override, IF(OR(s1="",s2=""),"",
          IF(s1>s2,team1, IF(s2>s1,team2,""))))``

    The override (the workbook's *Winner Override* column) decides ties that go
    to extra-time / penalties, since only 90-minute scores are entered.
    """
    if override:
        return override
    if not match_played(score1, score2):
        return None
    if score1 > score2:
        return team1
    if score2 > score1:
        return team2
    return None  # drawn after 90' and no override -> undecided


# --------------------------------------------------------------------------- #
#  Full knockout resolution
# --------------------------------------------------------------------------- #
@dataclass
class KnockoutMatch:
    number: int
    round: str
    team1: Optional[str] = None
    team2: Optional[str] = None
    score1: Optional[int] = None
    score2: Optional[int] = None
    override: Optional[str] = None
    winner: Optional[str] = None
    loser: Optional[str] = None
    pool: Optional[str] = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_bracket(standings: dict[str, list[TeamRow]], ko_results: dict,
                    knockout_meta: Optional[dict] = None) -> dict[int, KnockoutMatch]:
    """Resolve the whole knockout bracket from group standings + KO results.

    ``ko_results`` : {match_number: {"score1": int, "score2": int,
                                     "override": str}} (any field optional)

    Matches are resolved in ascending number order, which respects all
    dependencies (group seeding -> R32 -> R16 -> ... -> Final), mirroring the way
    the spreadsheet's later rows reference the ``Winner`` of earlier rows.
    """
    knockout_meta = knockout_meta or {}
    thirds = best_third_placed(standings)
    third_for_slot = assign_third_slots(thirds)

    matches: dict[int, KnockoutMatch] = {}

    def seed(spec, number) -> Optional[str]:
        kind = spec[0]
        if kind == "group":
            return group_winner(standings, spec[1], spec[2])
        if kind == "third":
            return third_for_slot.get(number)  # None -> unresolved (placeholder)
        if kind == "winner":
            src = matches.get(spec[1])
            return src.winner if src else None
        if kind == "loser":
            src = matches.get(spec[1])
            return src.loser if src else None
        return None

    for number in sorted(BRACKET):
        spec = BRACKET[number]
        res = ko_results.get(number) or ko_results.get(str(number)) or {}
        team1 = seed(spec["a"], number)
        team2 = seed(spec["b"], number)
        score1 = res.get("score1")
        score2 = res.get("score2")
        override = res.get("override") or None
        winner = resolve_winner(score1, score2, override, team1, team2)
        loser = None
        if winner is not None and team1 is not None and team2 is not None:
            loser = team2 if winner == team1 else team1
        matches[number] = KnockoutMatch(
            number=number, round=spec["round"], team1=team1, team2=team2,
            score1=score1, score2=score2, override=override,
            winner=winner, loser=loser, pool=spec.get("pool"),
            meta=knockout_meta.get(number) or knockout_meta.get(str(number)) or {},
        )
    return matches


# --------------------------------------------------------------------------- #
#  Sweepstake progress / leaderboard
# --------------------------------------------------------------------------- #
# Bonus points awarded to a team for *reaching* each stage, on top of the points
# it earns in the group table.  Deeper runs are rewarded progressively.  This is
# the sweepstake's own scoring (the workbook tracks the football; the prize money
# is awarded to the owners of the champion and runner-up — see ``prize_winners``).
STAGE_BONUS = {
    "Round of 32": 4,
    "Round of 16": 8,
    "Quarter-final": 16,
    "Semi-final": 24,
    "Final": 40,
    "Champion": 60,
}
# Stage order for "furthest reached" comparisons.
STAGE_ORDER = ["Group stage", "Round of 32", "Round of 16", "Quarter-final",
               "Semi-final", "Final", "Champion"]
# Winning a match in round X means you have *reached* the next round.
_ROUND_AFTER = {
    "Round of 32": "Round of 16",
    "Round of 16": "Quarter-final",
    "Quarter-final": "Semi-final",
    "Semi-final": "Final",
    "Final": "Champion",
}


def compute_progress(standings: dict[str, list[TeamRow]],
                     matches: dict[int, KnockoutMatch],
                     qualified_r32: set[str],
                     eliminated_in_group: set[str]) -> dict[str, dict]:
    """Per-team progress: group points, furthest stage reached and a score.

    Knockout credit is awarded only from *real* outcomes:

    * ``qualified_r32`` — teams that have officially reached the Round of 32
      (their group is complete and they finished top-2, or they are a qualified
      best-third with every group complete).  Computed by the caller, which owns
      the fixtures/results, so a mere bracket *projection* never inflates scores.
    * winning a decided knockout match promotes the winner to the next round.
    """
    progress: dict[str, dict] = {}
    for table in standings.values():
        for row in table:
            progress[row.country] = {
                "country": row.country,
                "group": row.group,
                "owner": row.owner,
                "owner_short": row.owner_short,
                "group_points": row.points,
                "stage": "Group stage",
                "eliminated": row.country in eliminated_in_group,
                "score": row.points,
            }

    for team in qualified_r32:
        if team in progress:
            _advance(progress[team], "Round of 32")

    # Winning a knockout match promotes you to the next round; the loser of a
    # decided match (other than the third-place play-off) is eliminated there.
    for number in sorted(BRACKET):
        m = matches[number]
        nxt = _ROUND_AFTER.get(m.round)
        if m.winner and nxt and m.winner in progress:
            _advance(progress[m.winner], nxt)
        if m.round != "Third Place" and m.loser and m.loser in progress:
            progress[m.loser]["eliminated"] = True
    return progress


def _advance(entry: dict, stage: str) -> None:
    cur = entry["stage"]
    if STAGE_ORDER.index(stage) > STAGE_ORDER.index(cur):
        entry["stage"] = stage
    entry["score"] = entry["group_points"] + sum(
        STAGE_BONUS.get(s, 0)
        for s in STAGE_ORDER[1:STAGE_ORDER.index(entry["stage"]) + 1]
    )


def leaderboard(progress: dict[str, dict]) -> list[dict]:
    """Aggregate team progress into a per-owner leaderboard, best-first."""
    owners: dict[str, dict] = {}
    for p in progress.values():
        o = owners.setdefault(p["owner"], {
            "owner": p["owner"], "score": 0, "teams": [],
            "alive": 0, "furthest": "Group stage",
        })
        o["score"] += p["score"]
        o["teams"].append(p)
        if not p["eliminated"]:
            o["alive"] += 1
        if STAGE_ORDER.index(p["stage"]) > STAGE_ORDER.index(o["furthest"]):
            o["furthest"] = p["stage"]
    for o in owners.values():
        o["teams"].sort(key=lambda t: (-t["score"], t["country"]))
    ranked = sorted(owners.values(), key=lambda o: (-o["score"], o["owner"]))
    for i, o in enumerate(ranked, 1):
        o["rank"] = i
    return ranked


def prize_winners(matches: dict[int, KnockoutMatch],
                  teams: list[dict], prize_pot: dict) -> dict:
    """Map the cash prizes to people.

    1st place -> owner of the champion (winner of the Final).
    2nd place -> owner of the runner-up (loser of the Final).
    The MIND charity share is fixed.
    """
    owner_of = {t["country"]: t["owner"] for t in teams}
    final = matches.get(104)
    champion = final.winner if final else None
    runner_up = final.loser if final else None
    out = {"champion": champion, "runner_up": runner_up, "awards": []}
    for prize in prize_pot.get("prizes", []):
        label = prize["label"]
        recipient = None
        if label.startswith("1st") and champion:
            recipient = owner_of.get(champion)
        elif label.startswith("2nd") and runner_up:
            recipient = owner_of.get(runner_up)
        out["awards"].append({**prize, "recipient": recipient})
    return out


# --------------------------------------------------------------------------- #
#  Group fixtures table — injected by the package on import (see __init__).
# --------------------------------------------------------------------------- #
_GROUP_FIXTURES: list[dict] = []


def set_group_fixtures(fixtures: list[dict]) -> None:
    """Register the group-fixture list used by :func:`group_standings`."""
    global _GROUP_FIXTURES
    _GROUP_FIXTURES = fixtures
