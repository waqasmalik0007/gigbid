# Single-container deployment for free hosts (e.g. a Hugging Face Docker Space, 16 GB RAM):
# web (Next.js, public port) -> /api proxied to FastAPI; GigSim, the worker loop and MCP servers
# (in-process transport) run alongside under supervisord. Postgres/Redis are external (Neon/Upstash)
# when DATABASE_URL / REDIS_URL are set; otherwise SQLite + in-process worker on the container disk.
FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
ENV NEXT_PUBLIC_API_URL="" API_INTERNAL_URL="http://127.0.0.1:8000" NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_XET=1 HF_HOME=/models \
    MCP_TRANSPORT=inproc GIGSIM_BASE_URL=http://127.0.0.1:8100 API_INTERNAL_URL=http://127.0.0.1:8000 \
    PORT=7860 HOSTNAME=0.0.0.0 NODE_ENV=production
RUN apt-get update && apt-get install -y --no-install-recommends curl supervisor ca-certificates \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 \
 && python -c "import tomllib;print('\n'.join(d for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'] if not d.startswith('torch')))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt
COPY . .
RUN python -c "from rag.index.embed import embed_docs, rerank_scores; embed_docs(['warm']); rerank_scores('a',['b'])"
COPY --from=web /web/.next/standalone /web
COPY --from=web /web/.next/static /web/.next/static
COPY infra/docker/supervisord.conf /etc/supervisor/conf.d/gigbid.conf
RUN useradd -m -u 1000 app && mkdir -p /app/data/processed && chown -R app /app /models /web
USER app
EXPOSE 7860
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/gigbid.conf"]
