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
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.split("#")[0].strip()
                if v and k.strip() not in os.environ:
                    os.environ[k.strip()] = v


_load_env()


@lru_cache
def config() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "agent" / "config.yaml").read_text())


def profile_name() -> str:
    return os.getenv("GIGBID_PROFILE") or config()["profile"]


def node_model(node: str) -> str:
    prof = config()["profiles"][profile_name()]
    nodes = prof["nodes"]
    return (nodes.get(node) or nodes.get("default") or next(iter(nodes.values())))["model"]


def _make(provider: str, model: str, temperature: float, max_tokens: int):
    if provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, temperature=temperature, max_tokens=max_tokens, timeout=60, max_retries=2)
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
    """Per-model sliding-window token budget (Groq free tier: 8k tokens/min per model).
    Sleeps before a call that would exceed TPM_LIMIT instead of provoking 429s."""

    def __init__(self) -> None:
        import threading
        from collections import defaultdict, deque
        self.lock = threading.Lock()
        self.win: dict[str, deque] = defaultdict(deque)

    def wait(self, model: str, est_tokens: int) -> None:
        limit = int(os.getenv("TPM_LIMIT", "7500"))
        while True:
            with self.lock:
                now, q = time.time(), self.win[model]
                while q and now - q[0][0] > 60:
                    q.popleft()
                used = sum(t for _, t in q)
                if used + est_tokens <= limit or not q:
                    q.append((now, est_tokens))
                    return
                sleep = 60 - (now - q[0][0]) + 0.2
            time.sleep(min(sleep, 30))


throttle = _Throttle()


def _estimate(messages: list, max_tokens: int) -> int:
    chars = sum(len(getattr(m, "content", str(m))) for m in messages)
    return chars // 3 + max_tokens  # Groq reserves the full max_tokens against TPM at request time


def get_llm(node: str, temperature: float = 0.2, max_tokens: int = 2048, fallback: bool = False):
    prof = config()["profiles"][profile_name()]
    model = prof.get("fallback_model") if fallback else node_model(node)
    return _make(prof["provider"], model, temperature, max_tokens)


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
        model = _model_of(llm)
        throttle.wait(model, _estimate(messages, max_tokens))
        t0 = time.perf_counter()
        try:
            runnable = llm.with_structured_output(schema, include_raw=True, method=method)
            out = runnable.invoke(messages)
            if out.get("parsed") is None:
                raise ValueError(f"unparseable output: {out.get('parsing_error')}")
            return out["parsed"], Usage.from_response(model, out["raw"], time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001 - retry/fallback on any model or parse failure
            last_err = e
    raise RuntimeError(f"{node}: structured output failed after retries and fallback: {last_err}")


def text(node: str, messages: list, temperature: float = 0.3, max_tokens: int = 1500) -> tuple[str, Usage]:
    last_err: Exception | None = None
    for fb in (False, True):
        llm = get_llm(node, temperature=temperature, max_tokens=max_tokens, fallback=fb)
        throttle.wait(_model_of(llm), _estimate(messages, max_tokens))
        t0 = time.perf_counter()
        try:
            msg = llm.invoke(messages)
            return msg.content, Usage.from_response(_model_of(llm), msg, time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"{node}: text call failed: {last_err}")


async def astructured(*args, **kwargs):
    """structured() in a worker thread, so parallel graph branches really run concurrently."""
    import asyncio
    return await asyncio.to_thread(structured, *args, **kwargs)


async def atext(*args, **kwargs):
    import asyncio
    return await asyncio.to_thread(text, *args, **kwargs)
