"""Build the R&D report PDF: docs/report/report.md -> HTML -> PDF (headless Chrome/Edge).

    python docs/report/build.py            # writes docs/report/GigBid_RnD_Report.pdf

Supports the Markdown subset the report uses: headings, paragraphs, bullet/numbered lists, tables,
fenced code, bold/italic/inline code, links, images, and {{results:<section>}} placeholders that are
filled from tools/results_tables.py so every number comes from evals/results/.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import results_tables as rt  # noqa: E402

SECTIONS = {"rag": rt.rag_table, "ablation": rt.ablation_table, "breakdown": rt.persona_table,
            "decision": rt.decision_table, "compare": rt.compare_table, "tools": rt.tool_table,
            "injection": rt.injection_table}


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="\2">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def md_to_html(md: str) -> str:
    md = re.sub(r"\{\{results:(\w+)\}\}", lambda m: SECTIONS[m.group(1)](), md)
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        if m := re.match(r"^(#{1,4})\s+(.*)", ln):
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
            continue
        elif re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            tag = "ol" if re.match(r"^\s*\d+\.", ln) else "ul"
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        elif ln.strip() == "<div class=\"pagebreak\"></div>":
            out.append(ln)
        elif ln.strip():
            para = [ln]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|\||```|\s*[-*]\s|\s*\d+\.\s)", lines[i + 1]):
                i += 1
                para.append(lines[i])
            out.append("<p>" + inline(" ".join(para)) + "</p>")
        i += 1
    return "\n".join(out)


CSS = """@page { size: A4; margin: 16mm 15mm; }
body { font: 10pt/1.45 Georgia, 'Times New Roman', serif; color: #111; }
h1 { font-size: 19pt; margin: 0 0 4pt; } h2 { font-size: 13pt; margin: 14pt 0 4pt; border-bottom: 1px solid #999; }
h3 { font-size: 11pt; margin: 10pt 0 3pt; } p { margin: 0 0 6pt; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt; font: 8pt/1.3 Arial, sans-serif; }
th, td { border: 1px solid #bbb; padding: 2pt 4pt; vertical-align: top; } th { background: #eee; }
code { font: 8.5pt Consolas, monospace; } pre { background: #f5f5f5; padding: 5pt; font-size: 8pt; white-space: pre-wrap; }
ul, ol { margin: 0 0 6pt 16pt; padding: 0; } .pagebreak { page-break-after: always; } img { max-width: 100%; }"""


def browser() -> str:
    for c in [os.getenv("CHROME"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", shutil.which("chromium"),
              shutil.which("google-chrome")]:
        if c and Path(c).exists():
            return c
    raise SystemExit("no Chrome/Edge found; set CHROME=<path>")


def main() -> None:
    md = (HERE / "report.md").read_text(encoding="utf-8")
    page = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{md_to_html(md)}</body></html>"
    html_path = HERE / "report.html"
    html_path.write_text(page, encoding="utf-8")
    pdf = HERE / "GigBid_RnD_Report.pdf"
    subprocess.run([browser(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={os.getenv('TMP', str(HERE))}/gigbid-chrome",
                    f"--print-to-pdf={pdf}", html_path.as_uri()], check=True, timeout=120)
    print(f"wrote {pdf}")


if __name__ == "__main__":
    main()
