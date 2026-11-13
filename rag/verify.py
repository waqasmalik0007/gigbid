"""B3 claim verification (R5 grounded claims).

split_claims(text)          -> experience-asserting sentences
verify_claims(claims, pipe) -> each claim supported / partial / unsupported, with evidence ids
strip_unsupported(draft)    -> remove unsupported sentences before the Critic sees the draft

A claim is SUPPORTED only if (a) every number and every known tool/skill it mentions appears in the
evidence it is matched to, and (b) the cross-encoder agrees the evidence is about the same thing.
Hard facts are checked lexically on purpose: an LLM verifier happily accepts "40%" vs "38%".
"""
from __future__ import annotations

import re

from agent.handoffs import Claim
from rag.index.embed import rerank_scores
from rag.requirements import ALL_SKILLS

CLAIM_HINT = re.compile(
    r"\b(I|I've|I have|my|we built|delivered|built|shipped|led|developed|designed|created|improved|reduced|"
    r"increased|cut|years? of experience|experience (with|in)|worked (with|on)|clients?)\b", re.I)
NUM = re.compile(r"\b\d+(?:\.\d+)?\s?%|\b\d{2,}\b|\b\d+\+?\s?(?:years?|yrs)\b", re.I)
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
SUPPORT_T, PARTIAL_T = 1.0, -2.0  # cross-encoder logits


def split_claims(text: str) -> list[str]:
    sents = [s.strip() for s in SENT.split(text.replace("\n", " ")) if s.strip()]
    return [s for s in sents if CLAIM_HINT.search(s) or NUM.search(s)]


def _facts(claim: str) -> tuple[set[str], set[str]]:
    nums = {n.replace(" ", "").lower() for n in NUM.findall(claim)}
    low = claim.lower()
    skills = {s for s in ALL_SKILLS if re.search(r"(?<![\w.])" + re.escape(s.lower()) + r"(?![\w])", low)}
    return nums, skills


def verify_claims(claims: list[str], pipe, cited: list[str] | None = None) -> list[Claim]:
    out = []
    items = pipe.items
    for c in claims:
        res = pipe.retrieve({"title": c, "description": c}, [c], k=6)
        cand = list(dict.fromkeys((cited or []) + [r.evidence_id for r in res.evidence]))[:10]
        cand = [e for e in cand if e in items and items[e].type != "past_proposal"]  # proposals aren't proof
        if not cand:
            out.append(Claim(text=c, status="unsupported", note="no evidence retrieved"))
            continue
        scores = rerank_scores(c, [items[e].text[:1500] for e in cand])
        ranked = sorted(zip(cand, scores), key=lambda x: -x[1])
        nums, skills = _facts(c)
        blob = " ".join(items[e].text.lower() + " " + " ".join(items[e].skills).lower() for e, _ in ranked[:4])
        blob_nums = {n.replace(" ", "").lower() for n in NUM.findall(blob)}
        missing_nums = {n for n in nums if n not in blob_nums and n.rstrip("%") not in blob}
        missing_sk = {s for s in skills if s.lower() not in blob}
        top = ranked[0][1]
        if missing_nums or (missing_sk and len(missing_sk) == len(skills) and skills):
            status, note = "unsupported", f"not in evidence: {sorted(missing_nums | missing_sk)}"
        elif top >= SUPPORT_T and not missing_sk:
            status, note = "supported", None
        elif top >= PARTIAL_T:
            status, note = "partial", (f"tools not evidenced: {sorted(missing_sk)}" if missing_sk else "weak match")
        else:
            status, note = "unsupported", "evidence does not match"
        out.append(Claim(text=c, evidence_ids=[e for e, s in ranked[:3] if s >= PARTIAL_T], status=status,
                         note=note))
    return out


def strip_unsupported(text: str, claims: list[Claim]) -> str:
    bad = {c.text for c in claims if c.status == "unsupported"}
    sents = [s for s in SENT.split(text.replace("\n\n", " \n\n ")) if s.strip() not in bad]
    return re.sub(r"[ \t]+", " ", " ".join(sents)).strip()


def unsupported_rate(claims: list[Claim]) -> float:
    return sum(c.status == "unsupported" for c in claims) / max(1, len(claims))
