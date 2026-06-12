"""Tests for the results feed (alias mapping + applying results to the store)."""
from wcsweepstake import feed


class FakeProvider:
    name = "fake"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self):
        return self.rows


class TestCanonicalTeam:
    def test_known_aliases(self):
        assert feed.canonical_team("South Korea") == "Korea Republic"
        assert feed.canonical_team("Turkey") == "Türkiye"
        assert feed.canonical_team("Iran") == "IR Iran"
        assert feed.canonical_team("Ivory Coast") == "Côte d'Ivoire"
        assert feed.canonical_team("DR Congo") == "Congo DR"
        assert feed.canonical_team("Columbia") == "Colombia"

    def test_canonical_passthrough_and_unknown(self):
        assert feed.canonical_team("Brazil") == "Brazil"
        assert feed.canonical_team("Atlantis") is None
        assert feed.canonical_team("") is None


class TestApplyFeed:
    def test_applies_group_result_with_orientation(self, store):
        # Fixture A1 is Mexico (home) v South Africa (away). Provider reports it
        # with teams the other way round; orientation must be preserved.
        provider = FakeProvider([
            {"home": "South Africa", "away": "Mexico", "home_score": 1,
             "away_score": 3, "status": "FINISHED"},
        ])
        summary = feed.apply_feed(store, provider)
        assert summary["updated"] == 1 and summary["ok"]
        # Stored in the fixture's orientation: Mexico 3-1 South Africa.
        assert store.group_results()["A1"] == {"home": 3, "away": 1}

    def test_skips_unfinished_and_reports_unmatched(self, store):
        provider = FakeProvider([
            {"home": "Mexico", "away": "South Africa", "home_score": None,
             "away_score": None, "status": "SCHEDULED"},
            {"home": "Atlantis", "away": "Narnia", "home_score": 1,
             "away_score": 0, "status": "FINISHED"},
        ])
        summary = feed.apply_feed(store, provider)
        assert summary["updated"] == 0
        assert summary["unmatched"] == ["Atlantis v Narnia"]

    def test_idempotent_no_double_count(self, store):
        rows = [{"home": "Mexico", "away": "South Africa", "home_score": 2,
                 "away_score": 0, "status": "FINISHED"}]
        provider = FakeProvider(rows)
        assert feed.apply_feed(store, provider)["updated"] == 1
        assert feed.apply_feed(store, provider)["updated"] == 0  # unchanged

    def test_sample_provider_fills_full_group_stage(self, store):
        summary = feed.apply_feed(store, feed.SampleProvider())
        assert summary["ok"] and summary["fetched"] == 72
        assert summary["updated"] == 72
        assert summary["unmatched"] == []  # every alias resolved
        store_last = store.last_feed()
        assert store_last["source"] == "sample"

    def test_provider_failure_is_logged_not_raised(self, store):
        class Boom:
            name = "boom"
            def fetch(self):
                raise RuntimeError("network down")
        summary = feed.apply_feed(store, Boom())
        assert summary["ok"] is False
        assert "network down" in summary["message"]
        assert store.last_feed()["ok"] is False

    def test_applies_knockout_result_after_groups(self, store):
        # Fill the whole group stage, then feed a Round-of-32 result and confirm
        # the feed locates the right knockout match by its (resolved) pairing.
        feed.apply_feed(store, feed.SampleProvider())
        from wcsweepstake import compute_state
        state = compute_state(store.group_results(), store.ko_results())
        r32 = next(m for m in state["bracket"] if m["round"] == "Round of 32"
                   and m["team1"] and m["team2"])
        provider = FakeProvider([
            {"home": r32["team1"], "away": r32["team2"], "home_score": 4,
             "away_score": 0, "status": "FINISHED"},
        ])
        summary = feed.apply_feed(store, provider)
        assert summary["updated"] == 1
        assert store.ko_results()[r32["number"]]["score1"] == 4
