"""Repository rules CI enforces (brief, "Rules your CI must enforce").

1. No secrets in the repo (API-key patterns, private keys, .env files).
2. Nothing under gigsim/ changed relative to the frozen manifest (gigsim/MANIFEST.sha256).
3. Every prompt lives in agent/prompts/: no long inline prompt strings handed to LLM messages.
4. Every number in README.md's results tables traces to a file in evals/results/.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r"(gsk_[A-Za-z0-9]{20,}|sk-(?:proj-)?(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{40,}|KGAT_[a-f0-9]{20,}|ghp_[A-Za-z0-9]{30,}|"
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16})")
errors: list[str] = []


def tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.splitlines()]


def check_secrets(files: list[Path]) -> None:
    for f in files:
        if f.name == ".env":
            errors.append(f"committed .env file: {f}")
        if f.suffix in {".png", ".jpg", ".pdf", ".npy", ".sqlite3", ".gif", ".zip"} or not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in SECRET.finditer(text):
            errors.append(f"possible secret in {f.relative_to(ROOT)}: {m.group(0)[:8]}…")


def check_gigsim() -> None:
    manifest = ROOT / "gigsim/MANIFEST.sha256"
    if not manifest.exists():
        errors.append("gigsim/MANIFEST.sha256 missing")
        return
    for line in manifest.read_text().splitlines():
        digest, rel = line.split(maxsplit=1)
        p = ROOT / rel
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            errors.append(f"gigsim changed without updating the frozen manifest: {rel}")


def check_prompts(files: list[Path]) -> None:
    inline = re.compile(r"SystemMessage\(\s*f?[\"']")  # a string literal, not fill("<prompt file>")
    for f in files:
        if f.suffix == ".py" and "agent/prompts" not in f.as_posix() and "tests" not in f.parts and f.exists():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if inline.search(line):
                    errors.append(f"inline system prompt (use agent/prompts/*.md): {f.relative_to(ROOT)}:{i}")


def check_readme_numbers() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""
    results = " ".join(p.read_text(encoding="utf-8") for p in (ROOT / "evals/results").glob("*.json"))
    for block in re.findall(r"<!-- results:start -->(.*?)<!-- results:end -->", readme, re.S):
        for num in re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", block):
            if num not in results and str(float(num)) not in results:
                errors.append(f"README number {num} not found in evals/results/*.json")


if __name__ == "__main__":
    files = tracked()
    check_secrets(files)
    check_gigsim()
    check_prompts(files)
    check_readme_numbers()
    for e in errors:
        print("FAIL:", e)
    print("ci_checks:", "FAILED" if errors else "ok")
    sys.exit(1 if errors else 0)
