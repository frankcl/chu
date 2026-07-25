"""Runtime session API tests."""


class TestCreateSession:
    def test_returns_session_id_with_default_react_mode(self, client):
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        assert resp.json()["session_id"]
        assert resp.json()["mode"] == "react"

    def test_plan_execute_mode(self, client):
        resp = client.post("/api/sessions", json={"mode": "plan-execute"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "plan-execute"

    def test_session_ids_are_unique(self, client):
        id1 = client.post("/api/sessions", json={}).json()["session_id"]
        id2 = client.post("/api/sessions", json={}).json()["session_id"]
        assert id1 != id2

    def test_invalid_memory_watermarks_are_rejected(self, client):
        resp = client.post("/api/sessions", json={
            "memory_max_tokens": 100,
            "memory_target_tokens": 100,
        })

        assert resp.status_code == 422
        assert "memory_target_tokens" in resp.json()["detail"]


class TestDeleteSession:
    def test_delete_existing_session_makes_chat_unavailable(self, client):
        session_id = client.post("/api/sessions", json={}).json()["session_id"]
        resp = client.delete(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        resp = client.post(f"/api/chat/{session_id}", json={"message": "hi"})
        assert resp.status_code == 404

    def test_delete_nonexistent_session_still_ok(self, client):
        resp = client.delete("/api/sessions/does-not-exist")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
