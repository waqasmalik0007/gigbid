"""The change request is absorbed by configuration: editing policy YAML alone changes Connects cost
(GigSim + dry_run preview) and the Compliance Guard's length limit. No code or prompt changes."""
import importlib

import yaml
from fastapi.testclient import TestClient

UPDATE = {"version": "2026-11-02", "starting_connects": 120, "connects_multiplier": 1.0,
          "premium_job_threshold": 1000, "premium_job_extra_connects": 4, "max_cover_letter_words": 250}


def test_policy_update_changes_connects_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGSIM_DB", str(tmp_path / "s.sqlite3"))
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    import gigsim.app as m
    m = importlib.reload(m)
    premium = next(j for j in m.world.history if (j.get("budget_max") or 0) > 1000)
    c = TestClient(m.app)
    h = {"Authorization": "Bearer sim-ai_ml_engineer"}
    before = c.get(f"/jobs/{premium['job_id']}", headers=h).json()["connects_cost"]
    pol = tmp_path / "policy.yaml"
    pol.write_text(yaml.safe_dump(UPDATE))
    monkeypatch.setenv("GIGSIM_POLICY_FILE", str(pol))
    after = c.get(f"/jobs/{premium['job_id']}", headers=h).json()["connects_cost"]
    assert after == before + 4
    long_letter = "word " * 260
    r = c.post("/proposals/drafts", headers=h | {"Idempotency-Key": "k1"},
               json={"job_id": premium["job_id"], "cover_letter": long_letter, "amount": 900})
    assert r.status_code == 422 and "250" in r.json()["error"]


def test_policy_pack_limit_change_blocks_long_letters(tmp_path, monkeypatch):
    import agent.nodes.compliance as comp
    pack = yaml.safe_load(open(comp.ROOT / "data/policy_pack/policy.yaml"))
    pack["limits"]["max_cover_letter_words"] = 250
    monkeypatch.setattr(comp, "policy", lambda: pack)
    text = "I built a churn model for a fintech client that raised precision 40%. " * 25  # ~300 words
    v = comp.check_text(text)
    assert not v.allowed and "250" in v.blocked_reason
