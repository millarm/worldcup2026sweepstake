"""Unit tests for the tournament engine.

Each test names the Excel formula (sheet *Tracker*) it pins down, so the suite
doubles as executable documentation of the spreadsheet's logic.
"""
import pytest

from wcsweepstake import engine


# --------------------------------------------------------------------------- #
#  Per-match primitives (Played / Home Pts / Away Pts / Result columns)
# --------------------------------------------------------------------------- #
class TestMatchPrimitives:
    def test_played_requires_both_scores(self):
        # Played = IF(AND(Home<>"", Away<>""), 1, "")
        assert engine.match_played(2, 1) is True
        assert engine.match_played(0, 0) is True
        assert engine.match_played(None, 1) is False
        assert engine.match_played(1, None) is False
        assert engine.match_played(None, None) is False

    @pytest.mark.parametrize("gf,ga,pts", [(3, 0, 3), (1, 1, 1), (0, 2, 0), (2, 2, 1)])
    def test_side_points(self, gf, ga, pts):
        # Home/Away Pts = IF(>, 3, IF(=, 1, 0))
        assert engine.side_points(gf, ga) == pts

    def test_result_letters(self):
        # Result = IF(>, "H", IF(=, "D", "A"))
        assert engine.match_result(2, 1) == "H"
        assert engine.match_result(1, 1) == "D"
        assert engine.match_result(0, 1) == "A"
        assert engine.match_result(None, 1) is None


# --------------------------------------------------------------------------- #
#  Group standings aggregation + ranking (GroupStandings table BD:BL)
# --------------------------------------------------------------------------- #
def _teams(*names, group="Z"):
    return [{"country": n, "group": group, "owner": n, "owner_short": n} for n in names]


class TestGroupStandings:
    def setup_method(self):
        # A self-contained 4-team group with its own fixtures.
        self.teams = _teams("Alpha", "Bravo", "Charlie", "Delta", group="Z")
        engine.set_group_fixtures([
            {"match": "Z1", "group": "Z", "home": "Alpha", "away": "Bravo"},
            {"match": "Z2", "group": "Z", "home": "Charlie", "away": "Delta"},
            {"match": "Z3", "group": "Z", "home": "Alpha", "away": "Charlie"},
            {"match": "Z4", "group": "Z", "home": "Bravo", "away": "Delta"},
            {"match": "Z5", "group": "Z", "home": "Alpha", "away": "Delta"},
            {"match": "Z6", "group": "Z", "home": "Bravo", "away": "Charlie"},
        ])

    def test_aggregation_counts_home_and_away(self):
        results = {
            "Z1": {"home": 2, "away": 0},  # Alpha beats Bravo
            "Z3": {"home": 1, "away": 1},  # Alpha draws Charlie
            "Z5": {"home": 0, "away": 3},  # Alpha loses to Delta
        }
        table = engine.group_standings(self.teams, results)["Z"]
        rows = {r.country: r for r in table}
        a = rows["Alpha"]
        assert (a.played, a.won, a.drawn, a.lost) == (3, 1, 1, 1)
        assert (a.gf, a.ga, a.gd, a.points) == (3, 4, -1, 4)
        # Delta only played once (away win at Alpha).
        assert (rows["Delta"].played, rows["Delta"].points, rows["Delta"].gf) == (1, 3, 3)

    def test_ranking_tiebreakers_pts_gd_gf_name(self):
        # Two teams level on points: GD then GF then name decide (BL formula).
        results = {
            "Z1": {"home": 3, "away": 0},  # Alpha 3-0 Bravo
            "Z2": {"home": 1, "away": 0},  # Charlie 1-0 Delta
            "Z3": {"home": 0, "away": 0},  # Alpha 0-0 Charlie
            "Z4": {"home": 0, "away": 0},  # Bravo 0-0 Delta
            "Z5": {"home": 0, "away": 0},  # Alpha 0-0 Delta
            "Z6": {"home": 0, "away": 0},  # Bravo 0-0 Charlie
        }
        table = engine.group_standings(self.teams, results)["Z"]
        order = [r.country for r in table]
        # Alpha & Charlie both 5 pts; Alpha GD +3 beats Charlie +1.
        assert order[0] == "Alpha"
        assert order[1] == "Charlie"

    def test_name_is_final_tiebreaker(self):
        # Nothing played -> all zero -> alphabetical (Team asc), per BL formula.
        table = engine.group_standings(self.teams, {})["Z"]
        assert [r.country for r in table] == ["Alpha", "Bravo", "Charlie", "Delta"]

    def test_group_winner_lookup(self):
        # Mirrors XLOOKUP(1,(Group=g)*(Rank=r),Team).
        standings = engine.group_standings(self.teams, {"Z1": {"home": 5, "away": 0}})
        assert engine.group_winner(standings, "Z", 1) == "Alpha"
        assert engine.group_winner(standings, "Z", 9) is None


# --------------------------------------------------------------------------- #
#  Best third-placed teams (A80 TAKE/SORTBY/FILTER over Rank=3)
# --------------------------------------------------------------------------- #
class TestBestThirdPlaced:
    def test_takes_top_n_thirds_by_points(self):
        # Build 3 groups, each with a clear 3rd-placed team of differing points.
        def row(country, group, won, gf, ga):
            r = engine.TeamRow(country=country, group=group)
            r.played, r.won, r.gf, r.ga = 3, won, gf, ga
            return r
        standings = {
            "A": [row("A1", "A", 3, 9, 0), row("A2", "A", 2, 5, 3), row("A3rd", "A", 1, 2, 4), row("A4", "A", 0, 0, 9)],
            "B": [row("B1", "B", 3, 9, 0), row("B2", "B", 2, 5, 3), row("B3rd", "B", 2, 6, 4), row("B4", "B", 0, 0, 9)],
            "C": [row("C1", "C", 3, 9, 0), row("C2", "C", 2, 5, 3), row("C3rd", "C", 0, 1, 7), row("C4", "C", 0, 0, 9)],
        }
        best = engine.best_third_placed(standings, take=2)
        names = [r.country for r in best]
        # B3rd (6 pts) and A3rd (3 pts) qualify; C3rd (0 pts) misses out.
        assert names == ["B3rd", "A3rd"]


# --------------------------------------------------------------------------- #
#  Knockout winner resolution (Winner column H)
# --------------------------------------------------------------------------- #
class TestResolveWinner:
    def test_higher_score_wins(self):
        assert engine.resolve_winner(2, 1, None, "X", "Y") == "X"
        assert engine.resolve_winner(0, 3, None, "X", "Y") == "Y"

    def test_override_beats_score(self):
        # Winner Override decides ties going to extra-time/penalties.
        assert engine.resolve_winner(1, 1, "Y", "X", "Y") == "Y"
        assert engine.resolve_winner(None, None, "X", "X", "Y") == "X"

    def test_draw_without_override_is_undecided(self):
        assert engine.resolve_winner(1, 1, None, "X", "Y") is None

    def test_unplayed_is_none(self):
        assert engine.resolve_winner(None, 1, None, "X", "Y") is None


# --------------------------------------------------------------------------- #
#  Bracket structure sanity (the C/F/H seeding + progression formulas)
# --------------------------------------------------------------------------- #
class TestBracketStructure:
    def test_has_all_rounds_and_counts(self):
        rounds = {}
        for spec in engine.BRACKET.values():
            rounds[spec["round"]] = rounds.get(spec["round"], 0) + 1
        assert rounds["Round of 32"] == 16
        assert rounds["Round of 16"] == 8
        assert rounds["Quarter-final"] == 4
        assert rounds["Semi-final"] == 2
        assert rounds["Third Place"] == 1
        assert rounds["Final"] == 1

    def test_exactly_eight_third_place_slots(self):
        third_slots = [m for m, s in engine.BRACKET.items()
                       if s.get("a", ("",))[0] == "third" or s.get("b", ("",))[0] == "third"]
        assert len(third_slots) == 8
        assert set(engine.THIRD_SLOT_GROUP) == set(third_slots)

    def test_progression_references_exist(self):
        # Every winner/loser source must point at an earlier, real match.
        for number, spec in engine.BRACKET.items():
            for side in ("a", "b"):
                kind, *rest = spec[side]
                if kind in ("winner", "loser"):
                    assert rest[0] in engine.BRACKET
                    assert rest[0] < number
