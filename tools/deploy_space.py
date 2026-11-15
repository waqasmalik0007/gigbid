"""Create or update the Hugging Face Docker Space and set its secrets (docs/DEPLOY.md, step 2-3 in one go).

    huggingface-cli login            # or: python -c "from huggingface_hub import login; login()"
    python tools/deploy_space.py <hf-username> [--space gigbid]

Reads GROQ_API_KEY / GROQ_API_KEY_2 / LANGSMITH_API_KEY / DATABASE_URL / REDIS_URL from the environment
or the git-ignored .env. Generates JWT_SECRET, TOKEN_ENCRYPTION_KEY, MCP_BEARER_TOKEN and, unless
REVIEWER_PASSWORD is set, a reviewer password, and writes the reviewer login to a file OUTSIDE the repo
(--creds, default ../REVIEWER_CREDENTIALS.txt) for the Classroom private comment. Nothing secret is
printed or committed. Uploads the output of tools/make_space.sh (git archive of HEAD + Dockerfile).
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dotenv() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env | {k: v for k, v in os.environ.items() if k in env or k.startswith(("GROQ_", "LANGSMITH_"))}


def main() -> None:
    from cryptography.fernet import Fernet
    from huggingface_hub import HfApi
    ap = argparse.ArgumentParser()
    ap.add_argument("user")
    ap.add_argument("--space", default="gigbid")
    ap.add_argument("--creds", default=str(ROOT.parent / "REVIEWER_CREDENTIALS.txt"))
    a = ap.parse_args()
    repo = f"{a.user}/{a.space}"
    env = dotenv()
    if not env.get("GROQ_API_KEY"):
        raise SystemExit("GROQ_API_KEY missing (.env or environment)")
    creds = Path(a.creds)
    email = env.get("REVIEWER_EMAIL") or "reviewer@gigbid.app"
    password = env.get("REVIEWER_PASSWORD")
    if not password and creds.exists():  # keep the password stable across redeploys
        for line in creds.read_text(encoding="utf-8").splitlines():
            if line.startswith("password: "):
                password = line.split(": ", 1)[1]
    password = password or secrets.token_urlsafe(12)
    sec = {"GROQ_API_KEY": env["GROQ_API_KEY"], "JWT_SECRET": secrets.token_urlsafe(48),
           "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "MCP_BEARER_TOKEN": secrets.token_urlsafe(32),
           "REVIEWER_EMAIL": email, "REVIEWER_PASSWORD": password}
    for k in ("GROQ_API_KEY_2", "LANGSMITH_API_KEY", "DATABASE_URL", "REDIS_URL"):
        v = env.get(k, "")
        if v and "://" in v and not any(h in v for h in ("localhost", "127.0.0.1", "host.docker.internal", "sqlite")):
            sec[k] = v  # only managed services; a local Postgres/Redis URL would break the Space
    api = HfApi()
    api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    for k, v in sec.items():
        api.add_space_secret(repo, k, v)
    if env.get("LANGSMITH_API_KEY"):
        api.add_space_variable(repo, "LANGSMITH_TRACING", "true")
        api.add_space_variable(repo, "LANGSMITH_PROJECT", "gigbid")
    out = Path(os.getenv("SPACE_DIR", ROOT.parent / "gigbid-space"))
    subprocess.run(["bash", "tools/make_space.sh", a.user, a.space], cwd=ROOT, check=True,
                   env=os.environ | {"SPACE_DIR": str(out)})
    api.upload_folder(repo_id=repo, repo_type="space", folder_path=str(out), ignore_patterns=[".git/*"],
                      commit_message="Deploy GigBid from GitHub HEAD")
    url = f"https://{a.user.lower()}-{a.space}.hf.space"
    creds.write_text(f"GigBid reviewer login (Classroom PRIVATE comment only; never commit)\nurl: {url}\n"
                     f"email: {email}\npassword: {password}\n", encoding="utf-8")
    print(f"Space: https://huggingface.co/spaces/{repo}\nApp (after the ~15 min build): {url}\n"
          f"Secrets set: {sorted(sec)}\nReviewer login written to {creds}")


if __name__ == "__main__":
    main()
