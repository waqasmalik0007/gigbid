"""B1 ingestion: load a persona's portfolio into evidence items with metadata on every chunk.

Sources: PDF CV (pypdf), Markdown case studies, repo READMEs and code, reviews, past proposals
(with outcome). GitHub repos can be added through the GitHub MCP server (rag/ingest/github.py).
Incremental re-indexing: items carry a content hash; only new/changed items are re-embedded.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ROOT / "data" / "personas"


@dataclass
class Item:
    """One portfolio evidence item (a case study, README, code file, review, proposal or CV)."""
    evidence_id: str
    persona: str
    type: str
    title: str
    text: str
    project_id: str | None = None
    skills: list[str] = field(default_factory=list)
    industry: str | None = None
    date: str | None = None
    outcome: str | None = None          # past proposals: won / replied / ignored
    source: str = ""
    synthetic: bool = True

    @property
    def hash(self) -> str:
        return hashlib.sha1(self.text.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self) | {"hash": self.hash}


@dataclass
class Chunk:
    chunk_id: str
    evidence_id: str
    text: str
    meta: dict


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def load_persona(persona: str) -> list[Item]:
    base = PERSONAS / persona
    idx = json.loads((base / "evidence_index.json").read_text(encoding="utf-8"))
    items: list[Item] = []
    for e in idx:
        text = e.get("text", "")
        src = f"data/personas/{persona}"
        if e["type"] == "cv" and (base / "cv.pdf").exists():
            text = read_pdf(base / "cv.pdf") or text  # exercise the PDF loader
            src += "/cv.pdf"
        items.append(Item(
            evidence_id=e["evidence_id"], persona=persona, type=e["type"],
            title=e.get("title") or e.get("job_title") or e["evidence_id"], text=text,
            project_id=e.get("project_id"), skills=e.get("skills") or [], industry=e.get("industry"),
            date=e.get("date"), outcome=e.get("outcome"), source=src, synthetic=e.get("synthetic", True)))
    for extra in sorted((base / "uploads").glob("*.md")) if (base / "uploads").exists() else []:
        items.append(Item(evidence_id=f"upload_{extra.stem}", persona=persona, type="upload",
                          title=extra.stem, text=extra.read_text(encoding="utf-8"), source=str(extra),
                          synthetic=False))
    removed = set(json.loads((base / "removed.json").read_text())) if (base / "removed.json").exists() else set()
    return [i for i in items if i.evidence_id not in removed]  # evidence the user removed is never retrieved


def remove_evidence(persona: str, evidence_id: str) -> None:
    """Remove one evidence item from the portfolio: an upload's file is deleted; built-in items are listed in
    removed.json so retrieval, the Fit Matcher and the claim verifier can no longer see them."""
    base = PERSONAS / persona
    if evidence_id.startswith("upload_"):
        f = base / "uploads" / f"{evidence_id.removeprefix('upload_')}.md"
        if f.exists():
            f.unlink()
            return
    path = base / "removed.json"
    removed = set(json.loads(path.read_text())) if path.exists() else set()
    path.write_text(json.dumps(sorted(removed | {evidence_id}), indent=1))


def fixed_chunks(items: list[Item], size: int = 400, overlap: int = 80) -> list[Chunk]:
    """V0 chunking: fixed character windows, ignoring item structure."""
    out = []
    for it in items:
        t = it.text
        for n, start in enumerate(range(0, max(1, len(t)), size - overlap)):
            piece = t[start:start + size]
            if piece.strip():
                out.append(Chunk(f"{it.evidence_id}#{n}", it.evidence_id, piece,
                                 {"type": it.type, "outcome": it.outcome, "project_id": it.project_id}))
    return out


def item_chunks(items: list[Item], prefix_fn=None) -> list[Chunk]:
    """One chunk per item (items are short); optional context prefix (V6)."""
    return [Chunk(f"{it.evidence_id}#0", it.evidence_id,
                  (prefix_fn(it) + "\n" if prefix_fn else "") + it.text[:2000],
                  {"type": it.type, "outcome": it.outcome, "project_id": it.project_id})
            for it in items]
