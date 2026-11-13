"""Insert the generated results tables into README.md between the results markers."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tables = subprocess.run([sys.executable, str(ROOT / "tools/results_tables.py")], capture_output=True, text=True,
                        check=True).stdout.split("\n", 2)[2]  # drop the title line
readme = (ROOT / "README.md").read_text(encoding="utf-8")
readme = re.sub(r"<!-- results:start -->.*?<!-- results:end -->",
                lambda _: "<!-- results:start -->\n" + tables.replace("## ", "### ") + "<!-- results:end -->",
                readme, flags=re.S)
(ROOT / "README.md").write_text(readme, encoding="utf-8")
print("README results updated")
