# One Python image for api, worker, gigsim and the three MCP servers (command chosen in compose).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_XET=1 HF_HOME=/models
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
# CPU-only torch keeps the image small enough for free hosts
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 \
 && python -c "import tomllib;print('\n'.join(d for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'] if not d.startswith('torch')))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt
COPY agent ./agent
COPY rag ./rag
COPY mcp_servers ./mcp_servers
COPY gigsim ./gigsim
COPY apps/api ./apps/api
COPY apps/__init__.py ./apps/__init__.py
COPY worker ./worker
COPY evals ./evals
COPY data/personas ./data/personas
COPY data/policy_pack ./data/policy_pack
COPY data/gigsim ./data/gigsim
COPY data/processed/market_stats.json ./data/processed/market_stats.json
# warm the open embedding + reranker models into the image
RUN python -c "from rag.index.embed import embed_docs, rerank_scores; embed_docs(['warm']); rerank_scores('a',['b'])"
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
