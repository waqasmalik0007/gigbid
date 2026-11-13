"""R7: outcomes and user edits update long-term memory ONLY through these explicit rules.

Style memory
  Each approved edit is diffed against the draft; detected patterns are stored as observations.
  RULE S1: a pattern is promoted to a style rule when it appears in >= STYLE_MIN_HITS of the user's
           last STYLE_WINDOW edits. Promoted rules carry the edit ids that justified them.
Outcome memory
  Every marketplace event (sent/viewed/replied/interview/hired/lost) updates counters per category
  and per proposal feature (length bucket, opening type).
  RULE O1: an insight is promoted when both compared buckets have >= OUTCOME_MIN_N proposals and their
           reply rates differ by >= OUTCOME_MIN_DIFF.
Raw tool output never enters memory; users can list and delete any promoted item (Settings screen).
"""
from __future__ import annotations

import difflib
import re
import time
import uuid

from agent.memory.store import get_store

STYLE_WINDOW, STYLE_MIN_HITS = 5, 2
OUTCOME_MIN_N, OUTCOME_MIN_DIFF = 5, 0.10
SENT = re.compile(r"(?<=[.!?])\s+")
FILLER = ["passionate", "i hope", "dear", "hello", "hi there", "i am confident", "perfect fit", "expert",
          "looking forward", "please feel free", "best regards", "kind regards", "thank you for"]

STYLE_TEXT = {
    "cut_first_sentence": "The freelancer usually deletes the first sentence: start with the second idea directly.",
    "shorter": "The freelancer shortens drafts: aim for about 25% fewer words.",
    "longer": "The freelancer lengthens drafts: add one more concrete detail per paragraph.",
    "no_signoff": "Do not end with a sign-off line.",
}


# ------------------------------------------------------------------ style
def edit_patterns(draft: str, edited: str) -> list[str]:
    pats: list[str] = []
    d_s, e_s = [s.strip() for s in SENT.split(draft) if s.strip()], [s.strip() for s in SENT.split(edited) if s.strip()]
    if d_s and e_s and d_s[0] not in edited and d_s[1:2] and e_s[0].startswith(d_s[1][:30]):
        pats.append("cut_first_sentence")
    dw, ew = len(draft.split()), len(edited.split())
    if ew < 0.8 * dw:
        pats.append("shorter")
    elif ew > 1.2 * dw:
        pats.append("longer")
    dl, el = draft.lower(), edited.lower()
    for f in FILLER:
        if f in dl and f not in el:
            pats.append(f"avoid:{f}")
    if re.search(r"(regards|cheers|best),?\s*\S*\s*$", dl) and not re.search(r"(regards|cheers|best),?\s*\S*\s*$", el):
        pats.append("no_signoff")
    return pats


def style_rule_text(pattern: str) -> str:
    if pattern.startswith("avoid:"):
        return f'Never say "{pattern[6:]}".'
    return STYLE_TEXT.get(pattern, pattern)


def record_edit(user: str, draft: str, edited: str, job_id: str) -> dict:
    """Store the observation, then apply RULE S1. Returns what was promoted (for the UI/trace)."""
    store = get_store()
    edit_id = "edit_" + uuid.uuid4().hex[:8]
    pats = edit_patterns(draft, edited)
    diff = "\n".join(difflib.unified_diff(draft.splitlines(), edited.splitlines(), lineterm="", n=0))[:4000]
    store.put(("style_obs", user), edit_id, {"ts": time.time(), "job_id": job_id, "patterns": pats, "diff": diff})
    recent = sorted(store.search(("style_obs", user), limit=200), key=lambda i: -i.value["ts"])[:STYLE_WINDOW]
    promoted = []
    for p in set(pats):
        hits = [i.key for i in recent if p in i.value["patterns"]]
        if len(hits) >= STYLE_MIN_HITS:
            key = "rule_" + re.sub(r"\W+", "_", p)
            store.put(("style", user), key, {"pattern": p, "text": style_rule_text(p), "evidence_edits": hits,
                                             "rule": f"S1: seen in {len(hits)} of last {STYLE_WINDOW} edits",
                                             "ts": time.time()})
            promoted.append(p)
    return {"edit_id": edit_id, "patterns": pats, "promoted": promoted}


def style_memory(user: str) -> list[dict]:
    return [i.value | {"key": i.key} for i in get_store().search(("style", user), limit=50)]


# ------------------------------------------------------------------ outcome
def _features(text: str) -> dict:
    words = len(text.split())
    first = (SENT.split(text.strip()) or [""])[0].lower()
    return {"length": "short(<160w)" if words < 160 else "long(>=160w)",
            "opening": "question" if first.endswith("?") else
                       "result-first" if re.search(r"\d|\[", first) else "other"}


def record_outcome(user: str, category: str, proposal_text: str, event: str) -> list[str]:
    """event in {sent, replied, hired}. Updates counters, then applies RULE O1."""
    store = get_store()
    ns = ("outcome_stats", user)
    feats = _features(proposal_text)
    for fname, fval in [("all", "all")] + list(feats.items()):
        key = f"{category}|{fname}|{fval}"
        cur = store.get(ns, key)
        v = cur.value if cur else {"sent": 0, "replied": 0, "hired": 0}
        v[event] = v.get(event, 0) + 1
        store.put(ns, key, v)
    return promote_outcomes(user, category)


def promote_outcomes(user: str, category: str) -> list[str]:
    store = get_store()
    stats = {i.key: i.value for i in store.search(("outcome_stats", user), limit=500) if i.key.startswith(category)}
    promoted = []
    for feature in ("length", "opening"):
        buckets = {k.split("|")[2]: v for k, v in stats.items() if k.split("|")[1] == feature}
        rates = {b: v["replied"] / v["sent"] for b, v in buckets.items() if v["sent"] >= OUTCOME_MIN_N}
        if len(rates) >= 2:
            best, worst = max(rates, key=rates.get), min(rates, key=rates.get)
            if rates[best] - rates[worst] >= OUTCOME_MIN_DIFF:
                text = (f"In {category}, {feature} '{best}' proposals got replies {rates[best]:.0%} of the time "
                        f"vs {rates[worst]:.0%} for '{worst}' (n={buckets[best]['sent']}/{buckets[worst]['sent']}).")
                store.put(("outcome", user), f"{category}|{feature}",
                          {"text": text, "category": category, "rule": "O1", "ts": time.time()})
                promoted.append(text)
    return promoted


def outcome_memory(user: str, category: str | None = None) -> list[dict]:
    items = [i.value | {"key": i.key} for i in get_store().search(("outcome", user), limit=50)]
    return [i for i in items if not category or i["category"] == category]


def delete_item(user: str, kind: str, key: str) -> None:
    get_store().delete((kind, user), key)
