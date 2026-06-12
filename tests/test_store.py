"""Tests for the SQLite persistence layer."""


class TestStore:
    def test_group_result_roundtrip_and_upsert(self, store):
        store.set_group_result("A1", 2, 1)
        assert store.group_results() == {"A1": {"home": 2, "away": 1}}
        store.set_group_result("A1", 0, 0)  # upsert overwrites
        assert store.group_results()["A1"] == {"home": 0, "away": 0}
        store.clear_group_result("A1")
        assert store.group_results() == {}

    def test_ko_result_roundtrip(self, store):
        store.set_ko_result(73, score1=1, score2=0)
        store.set_ko_result(74, override="Brazil")
        ko = store.ko_results()
        assert ko[73] == {"score1": 1, "score2": 0, "override": None}
        assert ko[74]["override"] == "Brazil"
        store.clear_ko_result(73)
        assert 73 not in store.ko_results()

    def test_feed_log(self, store):
        assert store.last_feed() is None
        store.log_feed("sample", 12, True, "ok")
        last = store.last_feed()
        assert last["source"] == "sample" and last["updated"] == 12 and last["ok"] is True

    def test_clear_all(self, store):
        store.set_group_result("A1", 1, 1)
        store.set_ko_result(73, 2, 1)
        store.log_feed("sample", 1, True)
        store.clear_all()
        assert store.group_results() == {} and store.ko_results() == {}
        assert store.last_feed() is None
