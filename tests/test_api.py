"""Tests for the Flask API routes."""


class TestReadRoutes:
    def test_health(self, client):
        data = client.get("/api/health").get_json()
        assert data["status"] == "ok"

    def test_index_served(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"Sweepstake" in res.data

    def test_state_shape(self, client):
        data = client.get("/api/state").get_json()
        for key in ("standings", "bracket", "leaderboard", "prizes", "prize_pot",
                    "third_placed", "people", "teams"):
            assert key in data
        assert len(data["standings"]) == 12
        assert len(data["teams"]) == 48

    def test_fixtures_and_bracket_and_leaderboard(self, client):
        assert len(client.get("/api/fixtures").get_json()["fixtures"]) == 72
        bracket = client.get("/api/bracket").get_json()
        assert len(bracket["bracket"]) == 32
        assert len(bracket["third_placed"]) == 8
        lb = client.get("/api/leaderboard").get_json()
        assert lb["prize_pot"]["total"] == 155


class TestWriteRoutes:
    def test_set_group_result_updates_state(self, client):
        res = client.post("/api/results/group", json={"match": "A1", "home": 3, "away": 0})
        assert res.status_code == 200
        groupA = res.get_json()["standings"]["A"]
        mexico = next(r for r in groupA if r["country"] == "Mexico")
        assert mexico["points"] == 3 and mexico["gf"] == 3

    def test_unknown_match_rejected(self, client):
        res = client.post("/api/results/group", json={"match": "Z9", "home": 1, "away": 0})
        assert res.status_code == 404

    def test_bad_payload_rejected(self, client):
        res = client.post("/api/results/group", json={"match": "A1"})
        assert res.status_code == 400

    def test_clear_group_result(self, client):
        client.post("/api/results/group", json={"match": "A1", "home": 3, "away": 0})
        res = client.delete("/api/results/group/A1")
        groupA = res.get_json()["standings"]["A"]
        assert all(r["played"] == 0 for r in groupA)

    def test_feed_refresh_populates(self, client):
        data = client.post("/api/feed/refresh").get_json()
        assert data["feed_summary"]["updated"] == 72
        # State now reflects a completed group stage.
        assert any(r["played"] > 0 for r in data["standings"]["A"])

    def test_reset(self, client):
        client.post("/api/feed/refresh")
        data = client.post("/api/admin/reset").get_json()
        assert all(r["played"] == 0 for rows in data["standings"].values() for r in rows)


class TestAdminAuth:
    def test_token_enforced_when_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "auth.db"))
        monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
        import importlib
        import app as app_module
        importlib.reload(app_module)
        c = app_module.app.test_client()
        # No token -> 401.
        assert c.post("/api/feed/refresh").status_code == 401
        # Correct token -> 200.
        ok = c.post("/api/feed/refresh", headers={"X-Admin-Token": "s3cret"})
        assert ok.status_code == 200
        # Reads stay open.
        assert c.get("/api/state").status_code == 200
