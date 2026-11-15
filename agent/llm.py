"""Model routing (Models section: "Route by node", "set in config, never in code").

    llm = get_llm("writer")                 # model chosen by agent/config.yaml profile
    out = structured(llm, Schema, messages)  # Pydantic output with retry + fallback model

Usage and cost per call are recorded so every run can report tokens and USD per node (R8).
"""
from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens (input, output). Groq list prices, Nov 2026; edit here, not in code.
PRICES = {
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "qwen/qwen3.8-27b": (0.29, 0.59),
}


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.split("#")[0].strip()
                if v and k.strip() not in os.environ:
                    os.environ[k.strip()] = v


_load_env()


@lru_cache
def config() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "agent" / "config.yaml").read_text(encoding="utf-8"))


def profile_name() -> str:
    return os.getenv("GIGBID_PROFILE") or config()["profile"]


def node_model(node: str) -> str:
    prof = config()["profiles"][profile_name()]
    nodes = prof["nodes"]
    return (nodes.get(node) or nodes.get("default") or next(iter(nodes.values())))["model"]


def groq_keys() -> list[str]:
    """GROQ_API_KEY, then GROQ_API_KEY_2 ... _5. Each key has its own per-model minute and day quotas."""
    names = ["GROQ_API_KEY"] + [f"GROQ_API_KEY_{i}" for i in range(2, 6)]
    return [os.environ[n] for n in names if os.getenv(n)]


def _make(provider: str, model: str, temperature: float, max_tokens: int, key: int = 0, **extra):
    if provider == "groq":
        from langchain_groq import ChatGroq
        keys = groq_keys()
        kw = ({"api_key": keys[key]} if keys else {}) | extra
        return ChatGroq(model=model, temperature=temperature, max_tokens=max_tokens, timeout=60, max_retries=1, **kw)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, temperature=temperature, num_predict=max_tokens,
                          base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    if provider in ("openai", "openrouter"):
        from langchain_openai import ChatOpenAI
        kw = {}
        if provider == "openrouter":
            kw = dict(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
        return ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens, timeout=60, **kw)
    raise ValueError(f"unknown provider {provider}")


class _Throttle:
    """Per-model sliding-window token AND request budget, shared by every process on the machine.

    Groq free tier: 8k tokens/min and ~30 requests/min per model. Each process used to keep its own window,
    so concurrent evaluations (or API + worker) overshot together. The window now lives in a small SQLite
    ledger (THROTTLE_DB) updated under an exclusive transaction, so all processes see one budget.
    """

    def __init__(self) -> None:
        import sqlite3
        import threading
        self.lock = threading.Lock()
        path = os.getenv("THROTTLE_DB", str(ROOT / "data/processed/throttle.sqlite3"))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS w (model TEXT, ts REAL, tokens INTEGER)")

    def wait(self, model: str, est_tokens: int) -> None:
        tpm = int(os.getenv("TPM_LIMIT", "7000"))
        rpm = int(os.getenv("RPM_LIMIT", "25"))
        while True:
            with self.lock:
                now = time.time()
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    self.db.execute("DELETE FROM w WHERE ts < ?", (now - 60,))
                    used, n, first = self.db.execute(
                        "SELECT COALESCE(SUM(tokens),0), COUNT(*), MIN(ts) FROM w WHERE model=?", (model,)).fetchone()
                    if n == 0 or (used + est_tokens <= tpm and n < rpm):
                        self.db.execute("INSERT INTO w VALUES (?,?,?)", (model, now, est_tokens))
                        self.db.execute("COMMIT")
                        return
                    self.db.execute("COMMIT")
                except Exception:
                    self.db.execute("ROLLBACK")
                    raise
                sleep = 60 - (now - first) + 0.3
            time.sleep(max(0.5, min(sleep, 20)))

    # Daily quotas (Groq free tier: 200k tokens/day per model). A model that returns a per-day 429 is
    # marked exhausted until the reset time Groq reports, and calls route to the next model in the
    # profile's quota_fallbacks list instead of retrying into the wall for minutes.
    def _ensure_ex(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS ex (model TEXT PRIMARY KEY, until REAL)")

    def exhausted(self, model: str) -> bool:
        with self.lock:
            self._ensure_ex()
            r = self.db.execute("SELECT until FROM ex WHERE model=?", (model,)).fetchone()
            return bool(r and r[0] > time.time())

    def mark_exhausted(self, model: str, seconds: float) -> None:
        with self.lock:
            self._ensure_ex()
            self.db.execute("INSERT OR REPLACE INTO ex VALUES (?,?)", (model, time.time() + seconds))

    def recent_tokens(self, slot: str) -> int:
        with self.lock:
            r = self.db.execute("SELECT COALESCE(SUM(tokens),0) FROM w WHERE model=? AND ts > ?",
                                (slot, time.time() - 60)).fetchone()
            return int(r[0])

    def next_reset(self) -> float:
        with self.lock:
            self._ensure_ex()
            r = self.db.execute("SELECT MIN(until) FROM ex WHERE until > ?", (time.time(),)).fetchone()
            return (r[0] - time.time()) if r and r[0] else 0.0


throttle = _Throttle()


class QuotaExhausted(RuntimeError):
    """Every model the node may use is out of daily quota; callers (evals) should stop and resume later."""


def _daily_quota_seconds(err: Exception) -> float | None:
    """Seconds until reset if `err` is a per-day rate limit (TPD/RPD), else None."""
    import re
    msg = str(err)
    if "per day" not in msg:
        return None
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", msg)
    if not m:
        return 600.0
    h, mi, se = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + se + 5


def _estimate(messages: list, max_tokens: int) -> int:
    chars = sum(len(getattr(m, "content", str(m))) for m in messages)
    return chars // 3 + max_tokens  # Groq reserves the full max_tokens against TPM at request time


def _route(model: str) -> tuple[str, int]:
    """(model, key index): the configured model on the API key with the most headroom this minute; when
    that model is out of daily quota on every key, the first quota fallback that still has quota.
    Throttle and quota bookkeeping use the slot name "model#key"."""
    n_keys = max(1, len(groq_keys()))
    prof = config()["profiles"][profile_name()]
    for cand in [model] + [m for m in prof.get("quota_fallbacks", []) if m != model]:
        free = [k for k in range(n_keys) if not throttle.exhausted(f"{cand}#{k}")]
        if not free:
            continue
        if cand != model:
            with (ROOT / "data/processed/quota_routes.log").open("a") as fh:  # reported in the evals
                tag = os.getenv("GIGBID_EVAL_TAG", "-")
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{tag}\t{model}\t{cand}\n")
        return cand, min(free, key=lambda k: throttle.recent_tokens(f"{cand}#{k}"))
    raise QuotaExhausted(f"all models out of daily quota; next reset in {throttle.next_reset():.0f}s")


_SLOTS: dict[int, str] = {}


def slot_of(llm) -> str:
    """The "model#key" throttle/quota slot of a client returned by get_llm."""
    return _SLOTS.get(id(llm), f"{_model_of(llm)}#0")


def get_llm(node: str, temperature: float = 0.2, max_tokens: int = 2048, fallback: bool = False):
    prof = config()["profiles"][profile_name()]
    model, key = _route(prof.get("fallback_model") if fallback else node_model(node))
    llm = _make(prof["provider"], model, temperature, max_tokens, key)
    if len(_SLOTS) > 5000:
        _SLOTS.clear()
    _SLOTS[id(llm)] = f"{model}#{key}"
    return llm


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


class Usage(dict):
    """{'tokens_in','tokens_out','usd','calls','latency_s'} accumulated per call."""

    @classmethod
    def from_response(cls, model: str, msg, latency: float) -> "Usage":
        um = getattr(msg, "usage_metadata", None) or {}
        ti, to = int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0))
        return cls(tokens_in=ti, tokens_out=to, usd=cost_usd(model, ti, to), calls=1, latency_s=latency)


def _model_of(llm) -> str:
    return getattr(llm, "model_name", None) or getattr(llm, "model", "") or ""


def structured(node: str, schema: type[T], messages: list, temperature: float = 0.1,
               max_tokens: int = 2048) -> tuple[T, Usage]:
    """Call the node's model for a Pydantic object; on failure retry once, then the fallback model.

    json_schema mode first: with function calling, gpt-oss sometimes replies with bare JSON instead
    of a tool call and Groq rejects it (400 tool_use_failed); measured on all hand-off schemas."""
    last_err: Exception | None = None
    for fb, method in ((False, "json_schema"), (False, "function_calling"), (True, "json_schema")):
        llm = get_llm(node, temperature=temperature, max_tokens=max_tokens, fallback=fb)
        model, slot = _model_of(llm), slot_of(llm)
        throttle.wait(slot, _estimate(messages, max_tokens))
        t0 = time.perf_counter()
        try:
            runnable = llm.with_structured_output(schema, include_raw=True, method=method)
            out = runnable.invoke(messages)
            if out.get("parsed") is None:
                raise ValueError(f"unparseable output: {out.get('parsing_error')}")
            return out["parsed"], Usage.from_response(model, out["raw"], time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001 - retry/fallback on any model or parse failure
            if (secs := _daily_quota_seconds(e)) is not None:
                throttle.mark_exhausted(slot, secs)
                return structured(node, schema, messages, temperature, max_tokens)  # re-route (or QuotaExhausted)
            last_err = e
    raise RuntimeError(f"{node}: structured output failed after retries and fallback: {last_err}")


def text(node: str, messages: list, temperature: float = 0.3, max_tokens: int = 1500) -> tuple[str, Usage]:
    last_err: Exception | None = None
    for fb in (False, True):
        llm = get_llm(node, temperature=temperature, max_tokens=max_tokens, fallback=fb)
        throttle.wait(slot_of(llm), _estimate(messages, max_tokens))
        t0 = time.perf_counter()
        try:
            msg = llm.invoke(messages)
            if not (msg.content or "").strip() and "gpt-oss" in _model_of(llm):
                # gpt-oss can spend the whole max_tokens on hidden reasoning (seen on job posts carrying
                # injected instructions) and return no text: retry once with low reasoning effort
                prof = config()["profiles"][profile_name()]
                slot = slot_of(llm)
                retry = _make(prof["provider"], _model_of(llm), temperature, max_tokens,
                              int(slot.rsplit("#", 1)[1]), reasoning_effort="low")
                throttle.wait(slot, _estimate(messages, max_tokens))
                msg = retry.invoke(messages)
                if not (msg.content or "").strip():
                    raise ValueError("empty completion (reasoning used the whole token budget)")
            return msg.content, Usage.from_response(_model_of(llm), msg, time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001
            if (secs := _daily_quota_seconds(e)) is not None:
                throttle.mark_exhausted(slot_of(llm), secs)
                return text(node, messages, temperature, max_tokens)
            last_err = e
    raise RuntimeError(f"{node}: text call failed: {last_err}")


async def astructured(*args, **kwargs):
    """structured() in a worker thread, so parallel graph branches really run concurrently."""
    import asyncio
    return await asyncio.to_thread(structured, *args, **kwargs)


async def atext(*args, **kwargs):
    import asyncio
    return await asyncio.to_thread(text, *args, **kwargs)
