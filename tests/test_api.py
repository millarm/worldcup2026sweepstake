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
    AUTH = {"X-Admin-Token": "test-admin-pw"}  # matches conftest's ADMIN_PASSWORD

    def test_set_group_result_updates_state(self, client):
        res = client.post("/api/results/group", json={"match": "A1", "home": 3, "away": 0},
                          headers=self.AUTH)
        assert res.status_code == 200
        groupA = res.get_json()["standings"]["A"]
        mexico = next(r for r in groupA if r["country"] == "Mexico")
        assert mexico["points"] == 3 and mexico["gf"] == 3

    def test_unknown_match_rejected(self, client):
        res = client.post("/api/results/group", json={"match": "Z9", "home": 1, "away": 0},
                          headers=self.AUTH)
        assert res.status_code == 404

    def test_bad_payload_rejected(self, client):
        res = client.post("/api/results/group", json={"match": "A1"}, headers=self.AUTH)
        assert res.status_code == 400

    def test_clear_group_result(self, client):
        client.post("/api/results/group", json={"match": "A1", "home": 3, "away": 0},
                    headers=self.AUTH)
        res = client.delete("/api/results/group/A1", headers=self.AUTH)
        groupA = res.get_json()["standings"]["A"]
        assert all(r["played"] == 0 for r in groupA)

    def test_feed_refresh_populates(self, client):
        data = client.post("/api/feed/refresh", headers=self.AUTH).get_json()
        assert data["feed_summary"]["updated"] == 72
        # State now reflects a completed group stage.
        assert any(r["played"] > 0 for r in data["standings"]["A"])

    def test_reset(self, client):
        client.post("/api/feed/refresh", headers=self.AUTH)
        data = client.post("/api/admin/reset", headers=self.AUTH).get_json()
        assert all(r["played"] == 0 for rows in data["standings"].values() for r in rows)


class TestAdminPassword:
    def test_admin_routes_require_password(self, client):
        # No password -> 401 on every write/feed route; reads stay open.
        assert client.post("/api/feed/refresh").status_code == 401
        assert client.post("/api/results/group",
                           json={"match": "A1", "home": 1, "away": 0}).status_code == 401
        assert client.post("/api/admin/reset").status_code == 401
        assert client.get("/api/state").status_code == 200

    def test_wrong_password_rejected(self, client):
        res = client.post("/api/feed/refresh", headers={"X-Admin-Token": "nope"})
        assert res.status_code == 401

    def test_login_endpoint(self, client):
        ok = client.post("/api/admin/login", json={"password": "test-admin-pw"})
        assert ok.status_code == 200 and ok.get_json()["ok"] is True
        bad = client.post("/api/admin/login", json={"password": "wrong"})
        assert bad.status_code == 401 and bad.get_json()["ok"] is False

    def test_password_accepted_in_json_body(self, client):
        res = client.post("/api/results/group",
                          json={"match": "A1", "home": 2, "away": 1, "admin_password": "test-admin-pw"})
        assert res.status_code == 200

    def test_builtin_default_password(self, tmp_path, monkeypatch):
        # With no env override, the built-in default password protects the panel.
        monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "auth.db"))
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        import importlib
        import app as app_module
        importlib.reload(app_module)
        c = app_module.app.test_client()
        assert c.post("/api/admin/login", json={"password": "BenEvesonIsInControl"}).status_code == 200
        # A write route (no network) is locked without the password, open with it.
        body = {"match": "A1", "home": 1, "away": 0}
        assert c.post("/api/results/group", json=body).status_code == 401
        assert c.post("/api/results/group", json=body,
                      headers={"X-Admin-Token": "BenEvesonIsInControl"}).status_code == 200

    def test_env_override_password(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "auth2.db"))
        monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
        import importlib
        import app as app_module
        importlib.reload(app_module)
        c = app_module.app.test_client()
        assert c.post("/api/admin/login", json={"password": "s3cret"}).status_code == 200
        assert c.post("/api/admin/login", json={"password": "BenEvesonIsInControl"}).status_code == 401
