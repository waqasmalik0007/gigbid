"""Write gigsim/MANIFEST.sha256 (run deliberately when GigSim is intentionally changed)."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
files = sorted(p for p in (ROOT / "gigsim").rglob("*") if p.is_file() and p.suffix in {".py", ".yaml"}
               and "__pycache__" not in p.parts)
lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT).as_posix()}" for p in files]
(ROOT / "gigsim/MANIFEST.sha256").write_text("\n".join(lines) + "\n")
print(f"froze {len(lines)} files")
