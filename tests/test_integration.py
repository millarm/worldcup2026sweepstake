"""End-to-end logic tests over the real tournament data (compute_state)."""
from wcsweepstake import DATA, compute_state, engine


def _full_group_stage():
    """A deterministic result for every one of the 72 group fixtures."""
    import hashlib
    results = {}
    for fx in DATA["fixtures"]:
        def g(a, b):
            return int(hashlib.md5(f"{a}|{b}".encode()).hexdigest()[:2], 16) % 4
        results[fx["match"]] = {"home": g(fx["home"], fx["away"]),
                                "away": g(fx["away"], fx["home"])}
    return results


class TestComputeState:
    def test_baseline_is_all_zero(self):
        state = compute_state()
        for rows in state["standings"].values():
            assert all(r["points"] == 0 and r["played"] == 0 for r in rows)
        # No one has advanced or scored before any games.
        assert all(o["score"] == 0 for o in state["leaderboard"])
        assert all(o["alive"] == len(o["teams"]) for o in state["leaderboard"])

    def test_full_group_stage_qualifies_two_per_group_plus_eight_thirds(self):
        state = compute_state(group_results=_full_group_stage())
        # Two direct qualifiers per group.
        for rows in state["standings"].values():
            assert [r["rank"] for r in rows] == [1, 2, 3, 4]
            assert sum(1 for r in rows if r["qualified"]) == 2
        # Eight best third-placed teams.
        assert len(state["third_placed"]) == 8
        # Every Round-of-32 slot now has a concrete team (canonical 3rd combo).
        r32 = [m for m in state["bracket"] if m["round"] == "Round of 32"]
        assert len(r32) == 16

    def test_leaderboard_rewards_progression(self):
        state = compute_state(group_results=_full_group_stage())
        board = state["leaderboard"]
        # Ranks are 1..N, sorted by score descending.
        assert [o["rank"] for o in board] == list(range(1, len(board) + 1))
        scores = [o["score"] for o in board]
        assert scores == sorted(scores, reverse=True)
        assert board[0]["score"] > 0

    def test_prizes_unassigned_until_final_decided(self):
        state = compute_state(group_results=_full_group_stage())
        assert state["prizes"]["champion"] is None
        assert all(a["recipient"] is None for a in state["prizes"]["awards"])

    def test_champion_takes_first_prize(self):
        groups = _full_group_stage()
        # Resolve the bracket far enough to know the two finalists, then play the
        # Final and assert the champion's owner receives the 1st-place prize.
        ko = {}
        # Advance the first-named team of every tie up to the Final, re-reading
        # the bracket each pass so newly-seeded ties get filled too. (team1 of a
        # match always resolves, so this completes the bracket for any draw.)
        for _ in range(10):
            state = compute_state(groups, ko)
            changed = False
            for m in state["bracket"]:
                if m["round"] == "Final" or m["number"] in ko:
                    continue
                advance = m["team1"] or m["team2"]
                if advance:
                    ko[m["number"]] = {"override": advance}
                    changed = True
            if not changed:
                break
        state = compute_state(groups, ko)
        final = next(m for m in state["bracket"] if m["round"] == "Final")
        assert final["team1"] and final["team2"]
        ko[final["number"]] = {"score1": 2, "score2": 1}  # team1 wins
        state = compute_state(groups, ko)
        champ = state["prizes"]["champion"]
        assert champ == final["team1"]
        owner_of = {t["country"]: t["owner"] for t in DATA["teams"]}
        first = next(a for a in state["prizes"]["awards"] if a["label"].startswith("1st"))
        assert first["recipient"] == owner_of[champ]
        # The champion is not marked eliminated; the runner-up is.
        champ_progress = next(t for o in state["leaderboard"] for t in o["teams"]
                              if t["country"] == champ)
        assert champ_progress["eliminated"] is False
        assert champ_progress["stage"] == "Champion"

    def test_prize_pot_totals_match_spreadsheet(self):
        pot = compute_state()["prize_pot"]
        assert pot["total"] == 155
        assert sum(p["amount"] for p in pot["prizes"]) == 155
        labels = {p["label"] for p in pot["prizes"]}
        assert labels == {"1st place", "2nd place", "MIND (charity)"}

    def test_knockout_override_resolves_drawn_tie(self):
        groups = _full_group_stage()
        state = compute_state(groups, {})
        tie = next(m for m in state["bracket"] if m["team1"] and m["team2"])
        # Drawn after 90' -> undecided without an override.
        state = compute_state(groups, {tie["number"]: {"score1": 1, "score2": 1}})
        decided = next(m for m in state["bracket"] if m["number"] == tie["number"])
        assert decided["winner"] is None
        # With an override the named team advances.
        state = compute_state(groups, {tie["number"]: {"score1": 1, "score2": 1,
                                                       "override": tie["team2"]}})
        decided = next(m for m in state["bracket"] if m["number"] == tie["number"])
        assert decided["winner"] == tie["team2"]
