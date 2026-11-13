# Deploying GigBid (free)

Target: a **Hugging Face Docker Space** (free CPU: 2 vCPU, 16 GB RAM), which fits the open embedding and
reranker models. One container runs the web app (port 7860), the API, GigSim and the MCP servers; Postgres
and Redis are optional managed services.

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
