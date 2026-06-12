"""Tests for the official FIFA Annex C third-place allocation table.

These guard the integrity of the 495-combination table and confirm the engine
seeds the Round-of-32 third-place slots from it correctly.
"""
import itertools

from wcsweepstake import engine

GROUPS = "ABCDEFGHIJKL"
# Official slot pools (FIFA Regulations §12.6), keyed by match number.
POOLS = {74: set("ABCDF"), 77: set("CDFGH"), 79: set("CEFHI"), 80: set("EHIJK"),
         81: set("BEFIJ"), 82: set("AEHIJ"), 85: set("EFGIJ"), 87: set("DEIJL")}


class TestAllocationTable:
    def test_all_495_combinations_present(self):
        expected = {"".join(c) for c in itertools.combinations(GROUPS, 8)}
        assert set(engine.THIRD_PLACE_ALLOCATION) == expected
        assert len(engine.THIRD_PLACE_ALLOCATION) == 495

    def test_every_value_is_a_permutation_of_its_key(self):
        for key, value in engine.THIRD_PLACE_ALLOCATION.items():
            assert sorted(value) == sorted(key), key

    def test_every_assignment_respects_official_pools(self):
        order = engine.THIRD_PLACE_MATCH_ORDER
        for key, value in engine.THIRD_PLACE_ALLOCATION.items():
            for match_no, group in zip(order, value):
                assert group in POOLS[match_no], f"{key}: {group} not in pool {match_no}"

    def test_canonical_combination_matches_workbook(self):
        # The workbook hard-codes combination A,B,C,D,F,G,K,L; the official table
        # must agree with it slot-for-slot.
        value = engine.THIRD_PLACE_ALLOCATION["ABCDFGKL"]
        from_table = dict(zip(engine.THIRD_PLACE_MATCH_ORDER, value))
        assert from_table == engine.THIRD_SLOT_GROUP


class TestAssignThirdSlots:
    def _thirds(self, groups):
        # Minimal TeamRow stand-ins (group + a distinct country name).
        return [engine.TeamRow(country=f"Team{g}", group=g) for g in groups]

    def test_uses_official_table_for_a_known_combination(self):
        # ABCDEFGH -> CFHEBAGD in match order (74,77,79,80,81,82,85,87).
        assigned = engine.assign_third_slots(self._thirds("ABCDEFGH"))
        expected_groups = dict(zip(engine.THIRD_PLACE_MATCH_ORDER, "CFHEBAGD"))
        for match_no, group in expected_groups.items():
            assert assigned[match_no] == f"Team{group}"

    def test_assignment_is_within_pools_for_random_combinations(self):
        for combo in ["EFGHIJKL", "ABCDEFKL", "ACEGIKBL", "BDFHJLAC"]:
            combo = "".join(sorted(set(combo)))[:8]
            assigned = engine.assign_third_slots(self._thirds(combo))
            for match_no, country in assigned.items():
                group = country.replace("Team", "")
                assert group in POOLS[match_no]

    def test_full_bracket_resolves_for_any_combination(self):
        # With the official table, all eight third-place slots fill for every
        # valid combination, so no Round-of-32 slot is left blank.
        for combo in ["ABCDEFGH", "EFGHIJKL", "ABEFIJKL", "CDEFGHIJ"]:
            assigned = engine.assign_third_slots(self._thirds(combo))
            assert len(assigned) == 8
            assert all(team is not None for team in assigned.values())
            assert len({t for t in assigned.values()}) == 8  # all distinct
