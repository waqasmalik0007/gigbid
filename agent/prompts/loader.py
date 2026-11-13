"""All prompts live in agent/prompts/<node>.md (versioned files, no inline prompts; CI checks this).

Each file starts with a front-matter line `version: N`. Untrusted text is always inserted inside
<untrusted_job_post> / <untrusted_client_message> delimiters by `fill`, never concatenated raw.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).parent


@lru_cache
def load(name: str) -> tuple[str, str]:
    raw = (DIR / f"{name}.md").read_text(encoding="utf-8")
    version = "0"
    if raw.startswith("---"):
        head, raw = raw[3:].split("---", 1)
        for line in head.splitlines():
            if line.strip().startswith("version:"):
                version = line.split(":", 1)[1].strip()
    return raw.strip(), version


def untrusted(tag: str, text: str) -> str:
    """Wrap untrusted data; strip any attempt to close our delimiter early."""
    safe = (text or "").replace(f"</{tag}>", "").replace(f"<{tag}>", "")
    return f"<{tag}>\n{safe}\n</{tag}>"


def fill(name: str, **values: str) -> str:
    tmpl, _ = load(name)
    for k, v in values.items():
        tmpl = tmpl.replace("{{" + k + "}}", str(v))
    return tmpl
