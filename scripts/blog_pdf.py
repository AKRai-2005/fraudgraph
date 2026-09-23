"""Typeset docs/BLOG.md as a print-quality PDF.

One source of truth: the Markdown. This converts the subset it uses, wraps it
in a print stylesheet built from the console's own tokens, and renders it with
headless Chromium -- which gives real page furniture, live links and proper
table breaking.

    python scripts/blog_pdf.py                 # -> build/fraudgraph-blog.pdf
    python scripts/blog_pdf.py --html          # keep the intermediate HTML too

Needs playwright (already used by the browser tests).
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The four figures that carry the piece, shown under the dek.
FIGURES = [
    ("18 of 20", "alerts where graph evidence moved the verdict"),
    ("9 of 9", "undocumented-typology frauds caught in replay"),
    ("0", "false fraud calls on 300 hard negatives"),
    ("310", "automated tests, 45 of them in a browser"),
]


# --------------------------------------------------------------- markdown
def inline(text: str) -> str:
    """Bold, italic, code, links -- escaped first, so content cannot inject."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"&lt;(https?://[^&\s]+)&gt;", r'<a href="\1">\1</a>', out)
    return out


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def convert(md: str) -> tuple[str, str, str]:
    """-> (title, dek, body html). The first H1 is the title, the italic line
    under it the dek; both are set by the template, not the body."""
    lines = md.replace("\r\n", "\n").split("\n")
    title, dek, out, i = "", "", [], 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("# ") and not title:
            title = line[2:].strip()
            i += 1
            continue
        # the dek is the italic block under the title, and it wraps over lines
        if title and not dek and line.startswith("*") and not line.startswith("**"):
            buf = []
            while i < len(lines) and lines[i].strip():
                buf.append(lines[i].strip())
                i += 1
            dek = " ".join(buf).strip("*").strip()
            continue
        # the masthead already carries the links, so drop the repeat
        if re.match(r"^Code:\s*<https://github", line):
            while i < len(lines) and lines[i].strip():
                i += 1
            continue

        if line.startswith("```"):                              # fenced code
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append('<pre class="code">' + html.escape("\n".join(buf)) + "</pre>")
            continue

        if line.strip() in ("---", "***", "___"):
            out.append('<hr class="sep">')
            i += 1
            continue

        m = re.match(r"^(#{2,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            head = cells(line)
            align = ["right" if c.strip().endswith(":") else "left" for c in cells(lines[i + 1])]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            th = "".join(f'<th style="text-align:{a}">{inline(c)}</th>'
                         for c, a in zip(head, align + ["left"] * len(head)))
            body = ""
            for r in rows:
                tds = "".join(f'<td style="text-align:{a}">{inline(c)}</td>'
                              for c, a in zip(r, align + ["left"] * len(r)))
                body += f"<tr>{tds}</tr>"
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")
            continue

        if re.match(r"^[*-]\s+", line):                          # bullet list
            items = []
            while i < len(lines) and re.match(r"^[*-]\s+", lines[i]):
                item = re.sub(r"^[*-]\s+", "", lines[i])
                i += 1
                while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                    item += " " + lines[i].strip()
                    i += 1
                items.append(f"<li>{inline(item)}</li>")
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", line):                         # numbered list
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                item = re.sub(r"^\d+\.\s+", "", lines[i])
                i += 1
                while i < len(lines) and lines[i].startswith("   ") and lines[i].strip():
                    item += " " + lines[i].strip()
                    i += 1
                items.append(f"<li>{inline(item)}</li>")
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        if not line.strip():
            i += 1
            continue

        para = [line]                                            # paragraph
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{2,4}\s|\||```|[*-]\s|\d+\.\s|---$)", lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")

    return title, dek, "\n".join(out)


# ------------------------------------------------------------------ page
CSS = """
@page { size: A4; margin: 20mm 19mm 18mm; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #fff; color: #1b1a17;
  font: 10.5pt/1.56 Charter, Cambria, Georgia, serif;
  -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums;
  text-rendering: geometricPrecision;
}
p { margin: 0 0 8pt; orphans: 3; widows: 3; hyphens: auto; }
strong { font-weight: 700; }
a { color: #274690; text-decoration: none; border-bottom: 0.4pt solid #b9c2d8; }
code { font: 9pt/1.4 Consolas, "Cascadia Mono", monospace; background: #f1efe9;
       padding: 0.5pt 2pt; border-radius: 2pt; }

/* masthead */
.eyebrow { font: 600 8pt/1.4 "Segoe UI", system-ui, sans-serif; letter-spacing: .14em;
           text-transform: uppercase; color: #66625a; margin: 0 0 6pt; }
h1 { font: 700 26pt/1.12 Charter, Cambria, Georgia, serif; letter-spacing: -.01em;
     margin: 0 0 8pt; text-wrap: balance; }
.dek { font: italic 12pt/1.45 Charter, Cambria, Georgia, serif; color: #4d4a43;
       margin: 0 0 12pt; max-width: 46em; }
.meta { font: 8.5pt/1.5 "Segoe UI", system-ui, sans-serif; color: #66625a;
        display: flex; gap: 18pt; flex-wrap: wrap; padding-bottom: 10pt;
        border-bottom: 1.6pt solid #1b1a17; }
.meta a { border: 0; color: #274690; }

/* the four figures under the dek */
.figures { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0;
           border-bottom: 0.6pt solid #dcd7cc; margin-bottom: 16pt; }
.figures div { padding: 10pt 12pt 10pt 0; }
.figures div + div { padding-left: 12pt; border-left: 0.6pt solid #dcd7cc; }
.figures b { display: block; font: 700 15pt/1.1 Charter, Cambria, Georgia, serif; }
.figures span { font: 8pt/1.35 "Segoe UI", system-ui, sans-serif; color: #66625a; }

/* headings */
h2 { font: 700 14pt/1.25 Charter, Cambria, Georgia, serif; margin: 15pt 0 7pt;
     padding-top: 7pt; border-top: 0.6pt solid #dcd7cc; break-after: avoid;
     break-inside: avoid; text-wrap: balance; }
h3 { font: 700 11pt/1.3 "Segoe UI", system-ui, sans-serif; margin: 14pt 0 5pt;
     break-after: avoid; }

/* lists */
ul, ol { margin: 0 0 10pt; padding-left: 15pt; }
li { margin-bottom: 4pt; orphans: 2; widows: 2; }
li::marker { color: #66625a; }

/* tables */
table { width: 100%; border-collapse: collapse; margin: 3pt 0 10pt;
        font: 9pt/1.45 "Segoe UI", system-ui, sans-serif; break-inside: avoid; }
th { font-size: 7.5pt; letter-spacing: .07em; text-transform: uppercase; color: #66625a;
     font-weight: 600; border-bottom: 1pt solid #1b1a17; padding: 5pt 7pt 4pt; }
td { padding: 5pt 7pt; border-bottom: 0.5pt solid #e6e1d7; vertical-align: top; }
tbody tr:last-child td { border-bottom: 0.8pt solid #b8b1a3; }
td strong { font-weight: 700; }

/* code blocks */
pre.code { font: 8.6pt/1.45 Consolas, "Cascadia Mono", monospace; background: #f7f5f0;
  border: 0.5pt solid #e6e1d7; border-left: 2pt solid #b8b1a3; border-radius: 2pt;
  padding: 7pt 10pt; margin: 4pt 0 10pt; white-space: pre-wrap; word-break: break-word;
  break-inside: avoid; color: #2a2823; }

hr.sep { border: 0; border-top: 0.6pt solid #dcd7cc; margin: 15pt 0; }
.figures + hr.sep { display: none; }
hr.sep + p:last-of-type { border-top: 0; padding-top: 0; margin-top: -4pt; }

/* the closing note, set apart */
body > p:last-of-type { font-style: italic; color: #4d4a43; font-size: 9.5pt;
                        border-top: 0.6pt solid #dcd7cc; padding-top: 9pt; }
"""

HEADER = """
<div style="font:7.5pt 'Segoe UI',sans-serif;color:#8a857c;width:100%;
            padding:0 19mm;display:flex;justify-content:space-between;">
  <span>{title}</span><span>github.com/AKRai-2005/fraudgraph</span>
</div>"""

FOOTER = """
<div style="font:7.5pt 'Segoe UI',sans-serif;color:#8a857c;width:100%;
            padding:0 19mm;display:flex;justify-content:space-between;">
  <span>TigerGraph &times; Hacker House Goa 2026</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>"""


def build_html(md_path: Path) -> tuple[str, str]:
    title, dek, body = convert(md_path.read_text(encoding="utf-8"))
    figs = "".join(f"<div><b>{html.escape(v)}</b><span>{html.escape(l)}</span></div>"
                   for v, l in FIGURES)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<p class="eyebrow">TigerGraph &times; Hacker House Goa 2026 &middot; Technical write-up</p>
<h1>{html.escape(title)}</h1>
<p class="dek">{inline(dek)}</p>
<div class="meta">
  <span>{date.today():%d %B %Y}</span>
  <span>Code: <a href="https://github.com/AKRai-2005/fraudgraph">github.com/AKRai-2005/fraudgraph</a></span>
  <span>Console: <a href="https://akrai-2005.github.io/fraudgraph/">akrai-2005.github.io/fraudgraph</a></span>
</div>
<div class="figures">{figs}</div>
{body}
</body></html>"""
    return title, page


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", default=str(REPO / "docs" / "BLOG.md"))
    ap.add_argument("--out", default=str(REPO / "build" / "fraudgraph-blog.pdf"))
    ap.add_argument("--html", action="store_true", help="keep the intermediate HTML")
    args = ap.parse_args(argv)

    title, page = build_html(Path(args.md))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = out.with_suffix(".html")
    html_path.write_text(page, encoding="utf-8")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(html_path.as_uri(), wait_until="networkidle")
        pg.pdf(path=str(out), format="A4", print_background=True,
               margin={"top": "22mm", "bottom": "18mm", "left": "19mm", "right": "19mm"},
               display_header_footer=True,
               header_template=HEADER.format(title=html.escape(title)),
               footer_template=FOOTER)
        browser.close()

    if not args.html:
        html_path.unlink(missing_ok=True)

    # document properties, so the file identifies itself in a reader
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(out))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({
        "/Title": title,
        "/Author": "Ashutosh Kumar Rai",
        "/Subject": "Agentic fraud investigation on TigerGraph -- "
                    "TigerGraph x Hacker House Goa 2026",
        "/Keywords": "TigerGraph, GSQL, graph database, fraud detection, "
                     "agentic AI, MCP, backtest",
        "/Creator": "scripts/blog_pdf.py",
    })
    with out.open("wb") as fh:
        writer.write(fh)

    print(f"{out}  --  {len(reader.pages)} pages, {out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
