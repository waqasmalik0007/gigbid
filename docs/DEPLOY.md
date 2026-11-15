# Deploying GigBid

**What the live link runs on (option B).** On 15 Nov, Hugging Face refused to create the Space: Docker
Spaces now need a PRO subscription (`402 Payment Required`). The free hosts I could find give 512 MB of RAM,
which is too little for the local embedding and reranker models. So the submitted live app runs on my own
machine and is exposed through a free Cloudflare quick tunnel.

## Option B: your own machine + Cloudflare tunnel (free, what the live link uses)

1. Make an isolated copy, so evaluation state never mixes with the live app:
   `git archive HEAD | tar -x -C <live-dir>`.
2. Write `<live-dir>/.env` with the secrets in the table below, and set `GIGSIM_BASE_URL=http://127.0.0.1:8101`
   and `MCP_TRANSPORT=inproc`.
3. Build the web app with `NEXT_PUBLIC_API_URL=""` and `API_INTERNAL_URL=http://127.0.0.1:8000`, then copy
   `.next/standalone` (plus `.next/static`) to `<live-dir>/web`.
4. Run `L=<live-dir> PY=python NODE=node CLOUDFLARED=cloudflared bash tools/serve_public.sh`.
   - It starts GigSim, the API, the web server and the tunnel.
   - It restarts any of them that exits.
   - It writes the current public URL to `<live-dir>/URL.txt`.

**Limits:**
- The machine must stay on and awake.
- A quick tunnel's URL changes if `cloudflared` restarts. A named tunnel on your own domain avoids
  this, but needs a Cloudflare account.

## Option A: Hugging Face Docker Space (needs HF PRO since Nov 2026)

`python tools/deploy_space.py <hf-username>` does steps 2–3 below in one go. It creates the Space, sets the
secrets, uploads `git archive HEAD` with `infra/docker/allinone.Dockerfile`, and writes the reviewer login to
a file outside the repo.

The Space is a single container on 2 vCPU and 16 GB RAM, enough for the open embedding and reranker models.
It runs the web app (port 7860), the API, GigSim and the MCP servers. Postgres and Redis are optional
managed services.

## 1. Accounts (one-time, ~10 minutes)

1. Hugging Face: sign up at huggingface.co, then **Settings → Access Tokens → New token** (type *Write*).
2. Optional but recommended, so data survives restarts: **Neon** (neon.tech) free Postgres: create a
   project and copy the connection string. **Upstash** (upstash.com) free Redis: copy the `rediss://` URL.

## 2. Create the Space

huggingface.co → **New Space** → name `gigbid`, SDK **Docker**, hardware **CPU basic (free)**, public.

In the Space: **Settings → Variables and secrets**, add as *secrets*:

| Name | Value |
|---|---|
| `GROQ_API_KEY` | your Groq key |
| `JWT_SECRET` | any long random string |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |
| `MCP_BEARER_TOKEN` | any long random string |
| `REVIEWER_EMAIL` | e.g. `reviewer@gigbid.app` |
| `REVIEWER_PASSWORD` | a password for the reviewer (put it only in the Classroom private comment) |
| `DATABASE_URL` | Neon connection string (optional; otherwise SQLite in the container) |
| `REDIS_URL` | Upstash URL (optional; otherwise agent jobs run inside the API process) |
| `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=gigbid` | from smith.langchain.com → Settings → API keys (free tier); every run is traced with tokens and cost per node |

## 3. Push

```bash
tools/make_space.sh <hf-username>
cd ../gigbid-space && git push space HEAD:main --force   # username + the HF write token as password
```

The first build takes ~10-15 minutes (it downloads the models into the image). The app is then at
`https://<hf-username>-gigbid.hf.space`. Sign in with the reviewer account.

## Keeping it live

Free Spaces sleep after 48 hours without visitors and wake on the next visit (about a minute). Visit the
link every day or two until grades are released, or use a free uptime pinger (e.g. UptimeRobot) on
`https://<hf-username>-gigbid.hf.space/login`.

## Local alternative

`docker compose -f infra/docker-compose.yml up --build` runs every service separately (web, api, worker,
three MCP servers, GigSim, Postgres, Redis, Qdrant; Ollama with `--profile local`).
