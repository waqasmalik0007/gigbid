"""Repo ingestion through the official GitHub MCP server (third-party server, A5 / B1).

    GITHUB_TOKEN=<fine-grained, read-only> python -m rag.ingest.github --persona me --repo owner/name [...]

Reads each repo's README and top-level source files with the GitHub MCP `get_file_contents` tool and
stores them as portfolio uploads (data/personas/<persona>/uploads/), which the Portfolio MCP server
indexes incrementally. Repo text is untrusted portfolio data: it is never executed or followed.
Transport: remote GitHub MCP (GITHUB_MCP_URL, default https://api.githubcopilot.com/mcp/).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = re.compile(r"\.(py|ts|tsx|js|ipynb|sql|go|rs)$")


async def ingest(persona: str, repos: list[str], max_files: int = 4) -> list[str]:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    token = os.environ["GITHUB_TOKEN"]
    client = MultiServerMCPClient({"github": {
        "transport": "streamable_http", "url": os.getenv("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/"),
        "headers": {"Authorization": f"Bearer {token}", "X-MCP-Readonly": "true"}}})
    tools = {t.name: t for t in await client.get_tools()}
    get = tools["get_file_contents"]
    out_dir = ROOT / "data/personas" / persona / "uploads"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for full in repos:
        owner, repo = full.split("/", 1)
        parts = [f"# {full}\n"]
        for path in ["README.md"]:
            try:
                parts.append(str(await get.ainvoke({"owner": owner, "repo": repo, "path": path}))[:6000])
            except Exception as e:  # noqa: BLE001
                parts.append(f"(no {path}: {e})")
        try:
            listing = await get.ainvoke({"owner": owner, "repo": repo, "path": "/"})
            names = [n for n in re.findall(r'"(?:name|path)":\s*"([^"]+)"', json.dumps(listing)) if SOURCE.search(n)]
            for n in names[:max_files]:
                code = str(await get.ainvoke({"owner": owner, "repo": repo, "path": n}))[:3000]
                parts.append(f"\n## {n}\n```\n{code}\n```")
        except Exception:  # noqa: BLE001
            pass
        f = out_dir / f"github_{owner}_{repo}.md"
        f.write_text("\n".join(parts), encoding="utf-8")
        written.append(str(f))
    from rag.retriever import invalidate
    invalidate(persona)
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True)
    ap.add_argument("--repo", action="append", required=True)
    a = ap.parse_args()
    print(asyncio.run(ingest(a.persona, a.repo)))
