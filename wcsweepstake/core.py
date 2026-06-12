"""High-level orchestration: turn stored results into the full site state.

This binds the static tournament data (:data:`wcsweepstake.DATA`) to the pure
functions in :mod:`wcsweepstake.engine` and produces the JSON-serialisable
structure the frontend renders.
"""
from __future__ import annotations

from . import DATA, engine


def _group_meta() -> dict:
    """Owner / paid lookup keyed by country."""
    return {t["country"]: t for t in DATA["teams"]}


def _completed_groups(group_results: dict) -> set[str]:
    """Groups in which all six fixtures have a result (so standings are final)."""
    played: dict[str, int] = {}
    total: dict[str, int] = {}
    for fx in DATA["fixtures"]:
        total[fx["group"]] = total.get(fx["group"], 0) + 1
        res = group_results.get(fx["match"]) or {}
        if engine.match_played(res.get("home"), res.get("away")):
            played[fx["group"]] = played.get(fx["group"], 0) + 1
    return {g for g, n in total.items() if played.get(g, 0) == n}


def fixtures_view(group_results: dict) -> list[dict]:
    """Group fixtures decorated with scores, played flag, result and points."""
    out = []
    for fx in DATA["fixtures"]:
        res = group_results.get(fx["match"]) or {}
        hs, as_ = res.get("home"), res.get("away")
        played = engine.match_played(hs, as_)
        out.append({
            **fx,
            "home_score": hs,
            "away_score": as_,
            "played": played,
            "result": engine.match_result(hs, as_),
            "home_points": engine.side_points(hs, as_) if played else None,
            "away_points": engine.side_points(as_, hs) if played else None,
        })
    return out


def compute_state(group_results: dict | None = None,
                  ko_results: dict | None = None) -> dict:
    """Compute the complete tournament state from stored results.

    ``group_results`` : {group_match_id: {"home": int, "away": int}}
    ``ko_results``     : {match_number: {"score1", "score2", "override"}}
    """
    group_results = group_results or {}
    ko_results = ko_results or {}
    meta = _group_meta()

    standings = engine.group_standings(DATA["teams"], group_results)
    # Decorate standings rows with owner + paid info and 1-based rank.
    standings_view: dict[str, list[dict]] = {}
    for group, rows in standings.items():
        table = []
        for i, row in enumerate(rows, 1):
            d = row.to_dict()
            d["rank"] = i
            d["paid"] = meta.get(row.country, {}).get("paid", False)
            d["qualified"] = i <= 2  # top two advance directly
            table.append(d)
        standings_view[group] = table

    thirds = engine.best_third_placed(standings)
    third_view = [
        {**r.to_dict(), "rank": i, "qualified": True}
        for i, r in enumerate(thirds, 1)
    ]

    completed = _completed_groups(group_results)
    all_complete = len(completed) == len(DATA["groups"])

    matches = engine.resolve_bracket(standings, ko_results, DATA.get("knockout_meta"))

    def _seed_locked(seed) -> bool:
        """Is a knockout seed's participant final (vs a live projection)?"""
        kind = seed[0]
        if kind == "group":
            return seed[1] in completed            # group table is final
        if kind == "third":
            return all_complete                    # best-thirds settled
        if kind in ("winner", "loser"):
            src = matches.get(seed[1])
            return bool(src and src.winner)         # feeding match decided
        return False

    bracket_view = []
    for n in sorted(matches):
        d = matches[n].to_dict()
        spec = engine.BRACKET[n]
        # Teams are "locked" only when both come from decided inputs, so the UI
        # can distinguish real upcoming ties from shifting projections.
        d["teams_locked"] = _seed_locked(spec["a"]) and _seed_locked(spec["b"])
        bracket_view.append(d)

    # Officially-decided Round-of-32 qualifiers (gates knockout scoring so a mere
    # bracket projection never awards progress points before games are played).
    qualified_r32: set[str] = set()
    eliminated_in_group: set[str] = set()
    for group, rows in standings.items():
        if group in completed:
            for i, row in enumerate(rows, 1):
                if i <= 2:
                    qualified_r32.add(row.country)
                elif i >= 4:
                    eliminated_in_group.add(row.country)
    if all_complete:
        third_qualified = {r.country for r in thirds}
        qualified_r32 |= third_qualified
        for r in (row for rows in standings.values() for row in rows):
            if r.country not in qualified_r32:
                eliminated_in_group.add(r.country)

    progress = engine.compute_progress(standings, matches, qualified_r32, eliminated_in_group)
    board = engine.leaderboard(progress)
    prizes = engine.prize_winners(matches, DATA["teams"], DATA["prize_pot"])

    return {
        "tournament": DATA["tournament"],
        "groups": DATA["groups"],
        "standings": standings_view,
        "third_placed": third_view,
        "fixtures": fixtures_view(group_results),
        "bracket": bracket_view,
        "leaderboard": board,
        "prize_pot": DATA["prize_pot"],
        "prizes": prizes,
        "people": DATA["people"],
        "teams": DATA["teams"],
    }
