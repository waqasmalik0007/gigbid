"""GigSim behaviour: auth, hidden prefs stay hidden, idempotency, Connects, dry_run, rate limit."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def sim(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGSIM_DB", str(tmp_path / "s.sqlite3"))
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    monkeypatch.setenv("GIGSIM_RATE_LIMIT_PER_MIN", "1000")
    import gigsim.app as m
    m = importlib.reload(m)
    m.world.sim_offset = 10 * 24 * 3600  # release some stream jobs
    return m, TestClient(m.app)


H = {"Authorization": "Bearer sim-ai_ml_engineer"}


def test_auth_required(sim):
    _, c = sim
    assert c.get("/jobs").status_code == 401


def test_hidden_preferences_never_exposed(sim):
    _, c = sim
    job = c.get("/jobs", headers=H).json()["jobs"][0]
    cl = c.get(f"/clients/{job['client_id']}", headers=H).json()
    assert "hidden" not in cl and "history" in cl


def test_submit_is_idempotent_and_spends_connects_once(sim):
    _, c = sim
    job = c.get("/jobs", headers=H).json()["jobs"][0]
    body = {"job_id": job["job_id"], "cover_letter": "I built a RAG assistant that answered 300 questions.",
            "amount": 500}
    d1 = c.post("/proposals/drafts", json=body, headers=H | {"Idempotency-Key": "d1"}).json()
    d2 = c.post("/proposals/drafts", json=body, headers=H | {"Idempotency-Key": "d1"}).json()
    assert d1["proposal_id"] == d2["proposal_id"] and d2["idempotent_replay"]
    before = c.get("/connects", headers=H).json()["balance"]
    preview = c.post(f"/proposals/{d1['proposal_id']}/submit?dry_run=true", headers=H).json()
    assert preview["dry_run"] and c.get("/connects", headers=H).json()["balance"] == before
    for _ in range(3):  # retried submit
        c.post(f"/proposals/{d1['proposal_id']}/submit", headers=H | {"Idempotency-Key": "s1"})
    after = c.get("/connects", headers=H).json()["balance"]
    assert before - after == preview["connects_cost"]


def test_write_requires_idempotency_key(sim):
    _, c = sim
    job = c.get("/jobs", headers=H).json()["jobs"][0]
    r = c.post("/proposals/drafts", json={"job_id": job["job_id"], "cover_letter": "x" * 30, "amount": 5}, headers=H)
    assert r.status_code == 400


def test_rate_limit(sim, monkeypatch):
    m, c = sim
    monkeypatch.setattr(m, "RATE", 3)
    codes = [c.get("/connects", headers=H).status_code for _ in range(5)]
    assert 429 in codes


def test_generic_opening_scores_lower():
    from gigsim.client_sim import score_proposal
    job = {"skills": ["RAG", "Qdrant"], "budget_max": 1000}
    client = {"hidden": {"must_have": ["RAG", "FastAPI"], "budget_ceiling_factor": 1.0,
                         "dislikes_generic_opening": True, "prefers_short": False, "responsiveness": 0.9}}
    good = score_proposal(job, client, {"cover_letter": "You need RAG over Qdrant; I built a FastAPI RAG "
                                        "assistant that cut support time 40%. " * 3, "amount": 800})
    bad = score_proposal(job, client, {"cover_letter": "Dear Sir/Madam, I am very passionate about this job. " * 3,
                                       "amount": 800})
    assert good["p_reply"] > bad["p_reply"]
