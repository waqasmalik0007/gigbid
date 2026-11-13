"""API contract without LLMs: auth, tenant isolation, settings, memory viewer, approval validation."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGBID_APP_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("GIGBID_MEMORY_FILE", str(tmp_path / "mem.json"))
    monkeypatch.setenv("GIGBID_CHECKPOINT_FILE", str(tmp_path / "cp.pkl"))
    monkeypatch.setenv("REVIEWER_PASSWORD", "reviewer-pass-123")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import apps.api.db as db
    db._conn.cache_clear()
    import agent.memory.store as st
    st.get_store.cache_clear()
    st.get_checkpointer.cache_clear()
    import worker.runner as runner
    runner._graphs.clear()
    import apps.api.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    db._conn.cache_clear()


def signup(c, email):
    r = c.post("/api/auth/signup", json={"email": email, "password": "password123", "persona_id": "data_analyst"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_flow_and_reviewer_seed(client):
    h = signup(client, "a@example.com")
    assert client.get("/api/auth/me", headers=h).json()["persona_id"] == "data_analyst"
    assert client.post("/api/auth/signup", json={"email": "a@example.com", "password": "password123"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/signup", json={"email": "not-an-email", "password": "password123"}).status_code == 422
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs", headers={"Authorization": "Bearer forged"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "reviewer@gigbid.local", "password": "reviewer-pass-123"})
    assert r.status_code == 200 and r.json()["user"]["persona_id"] == "ai_ml_engineer"


def test_settings_and_memory_viewer(client):
    h = signup(client, "s@example.com")
    r = client.put("/api/auth/settings", headers=h, json={"floor_rate": 45, "autonomy": "auto_skip_low_fit"})
    assert r.json()["settings"]["floor_rate"] == 45
    assert client.put("/api/auth/settings", headers=h, json={"autonomy": "auto_send"}).status_code == 422  # never
    assert client.get("/api/memory", headers=h).json() == {"style": [], "outcome": []}


def test_tenant_isolation_on_threads(client):
    ha = signup(client, "a@example.com")
    hb = signup(client, "b@example.com")
    uid_a = client.get("/api/auth/me", headers=ha).json()["id"]
    tid = f"bid:{uid_a}:job_x"
    # B cannot read or decide on A's thread
    assert client.get(f"/api/approvals/{tid}/payload", headers=hb).status_code == 404
    assert client.post(f"/api/approvals/{tid}", headers=hb, json={"action": "approve"}).status_code == 404
    assert client.get(f"/api/runs/{tid}/events", headers=hb).json() == []
    # A gets a clear 404/409 when nothing is pending
    assert client.get(f"/api/approvals/{tid}/payload", headers=ha).status_code == 404
    assert client.post(f"/api/approvals/{tid}", headers=ha, json={"action": "approve"}).status_code == 409


def test_job_detail_requires_own_inbox(client):
    h = signup(client, "j@example.com")
    assert client.get("/api/jobs", headers=h).json() == []
    assert client.get("/api/jobs/job_nope", headers=h).status_code == 404
    assert client.get("/openapi.json").status_code == 200
