"""Viva Q3: evidence removed from the portfolio is never retrieved again, so a claim that relied on it
can no longer be verified against it. Runs on a temporary copy of a persona (no LLM calls)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import rag.ingest.loaders as loaders

ROOT = Path(__file__).resolve().parents[2]


def test_removed_evidence_is_not_retrieved(tmp_path, monkeypatch):
    src = ROOT / "data/personas/data_analyst"
    dst = tmp_path / "data_analyst"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("cv.pdf", "repos"))
    monkeypatch.setattr(loaders, "PERSONAS", tmp_path)
    items = loaders.load_persona("data_analyst")
    case = next(i for i in items if i.type == "case_study")
    assert case.evidence_id in {i.evidence_id for i in items}

    loaders.remove_evidence("data_analyst", case.evidence_id)
    assert json.loads((dst / "removed.json").read_text()) == [case.evidence_id]
    after = loaders.load_persona("data_analyst")
    assert case.evidence_id not in {i.evidence_id for i in after}
    assert len(after) == len(items) - 1


def test_removing_an_upload_deletes_its_file(tmp_path, monkeypatch):
    dst = tmp_path / "data_analyst"
    shutil.copytree(ROOT / "data/personas/data_analyst", dst, ignore=shutil.ignore_patterns("cv.pdf", "repos"))
    (dst / "uploads").mkdir(exist_ok=True)
    (dst / "uploads" / "dash_abc123.md").write_text("# Dashboard\nBuilt a Power BI dashboard.", encoding="utf-8")
    monkeypatch.setattr(loaders, "PERSONAS", tmp_path)
    assert "upload_dash_abc123" in {i.evidence_id for i in loaders.load_persona("data_analyst")}
    loaders.remove_evidence("data_analyst", "upload_dash_abc123")
    assert not (dst / "uploads" / "dash_abc123.md").exists()
    assert "upload_dash_abc123" not in {i.evidence_id for i in loaders.load_persona("data_analyst")}
