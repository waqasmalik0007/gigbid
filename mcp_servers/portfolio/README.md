# gigbid-portfolio-mcp

An MCP server that turns **your portfolio** (Markdown, text or PDF files: case studies, CV, READMEs, past
proposals) into evidence an AI assistant can cite when it writes proposals or answers screening
questions. Read-only. Works with Claude Desktop, Cursor and any MCP host.

## Tools

| Tool | What it does |
|---|---|
| `search_evidence(requirement, k=5, filters=None)` | Hybrid (BM25 + dense) search over contextualised chunks for ONE requirement; returns evidence ids, type, source, date, outcome |
| `get_case_study(evidence_id)` | Full text of one item + related items of the same project |
| `list_skills()` | Skills your portfolio can actually evidence, with counts |

Resources: `portfolio://case/{id}`, `portfolio://proposal/{id}`.
Prompts: "Answer a screening question from my portfolio", "Write a proposal opening grounded in my
best matching project".

## Install

Point it at a folder of your files (`proposals/` sub-folder = past proposals):

```bash
# uvx, straight from the public repository (first run installs ~110 packages incl. PyTorch;
# the server indexes the folder at startup, ~30-60 s, then each search takes about a second)
PORTFOLIO_DIR=~/my-portfolio uvx --from "git+https://github.com/waqasmalik0007/gigbid" gigbid-portfolio-mcp

# Docker
# Docker, from a clone of the repository (the shared Python image; override its command)
docker build -f infra/docker/python.Dockerfile -t gigbid-py .
docker run -i --rm -v ~/my-portfolio:/portfolio -e PORTFOLIO_DIR=/portfolio gigbid-py python -m mcp_servers.portfolio.server
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "portfolio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/waqasmalik0007/gigbid", "gigbid-portfolio-mcp"],
      "env": { "PORTFOLIO_DIR": "/Users/me/my-portfolio" }
    }
  }
}
```

Over HTTP (bearer auth, rate limit, audit log): `MCP_BEARER_TOKEN=... gigbid-portfolio-mcp --http 8201`.

## Notes

* Embeddings (BGE-small) and the optional reranker are open models that run on CPU; the first run
  downloads them (~200 MB).
* Your files never leave your machine; nothing is uploaded.
* Proposals found by search are returned as *phrasing examples with outcomes*; only case studies, code,
  reviews and your CV count as proof of experience.
