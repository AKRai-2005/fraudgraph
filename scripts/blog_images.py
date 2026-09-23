"""Make the images for the blog post: a cover, and three from the console.

The screenshots are taken from the static export, so what a reader sees in the
post is the same page they can open at the Console link -- not a mockup.

    python scripts/export_static.py       # first, if the export is stale
    python scripts/blog_images.py         # -> docs/img/*.png
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socket
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "build" / "static_site"
OUT = REPO / "docs" / "img"

COVER = """<!doctype html><html><head><meta charset="utf-8"><style>
:root { --paper:#f6f4ee; --ink:#1b1a17; --ink-2:#4d4a43; --ink-3:#66625a;
        --rule:#dcd7cc; --fraud:#a3261b; --legit:#2c6639; --accent:#274690; }
* { box-sizing: border-box; margin: 0; }
body { width: 1000px; height: 420px; background: var(--paper); color: var(--ink);
       font: 16px/1.5 system-ui, "Segoe UI", sans-serif; padding: 40px 48px;
       display: flex; flex-direction: column; justify-content: space-between; }
.eyebrow { font-size: 12px; letter-spacing: .16em; text-transform: uppercase;
           color: var(--ink-3); font-weight: 600; }
h1 { font: 700 46px/1.1 Charter, Cambria, Georgia, serif; letter-spacing: -.01em;
     margin: 14px 0 10px; max-width: 18ch; }
h1 .n { color: var(--fraud); }
p.dek { font: italic 19px/1.4 Charter, Cambria, Georgia, serif; color: var(--ink-2);
        max-width: 60ch; }
.figs { display: flex; gap: 0; border-top: 1px solid var(--rule); padding-top: 14px; }
.figs div { padding-right: 26px; margin-right: 26px; border-right: 1px solid var(--rule); }
.figs div:last-child { border-right: 0; }
.figs b { display: block; font: 700 24px/1 Charter, Cambria, Georgia, serif; }
.figs b.up { color: var(--legit); }
.figs span { font-size: 12px; color: var(--ink-3); }
.foot { display: flex; justify-content: space-between; align-items: baseline;
        font-size: 13px; color: var(--ink-3); }
.foot b { color: var(--accent); font-weight: 600; }
</style></head><body>
<div>
  <p class="eyebrow">TigerGraph &times; Hacker House Goa 2026</p>
  <h1>Our fraud classifier scored <span class="n">0.963 AUC</span>. We threw it away.</h1>
  <p class="dek">Building an agentic fraud investigator on a 590,742-transaction graph —
     and the number we had to withdraw.</p>
</div>
<div class="figs">
  <div><b>18 of 20</b><span>alerts the graph moved</span></div>
  <div><b class="up">9 of 9</b><span>undocumented frauds caught</span></div>
  <div><b>0</b><span>false fraud calls on 300</span></div>
  <div><b>310</b><span>automated tests</span></div>
</div>
<div class="foot"><span>github.com/<b>AKRai-2005/fraudgraph</b></span>
  <span>TigerGraph Savanna &middot; GSQL &middot; MCP &middot; Gemini</span></div>
</body></html>"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if not (SITE / "index.html").exists():
        print("No static export at build/static_site — run scripts/export_static.py first.")
        return 2

    port = free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright

    made = []
    with sync_playwright() as p:
        b = p.chromium.launch()

        # 1. the cover, at Dev.to's 1000x420
        page = b.new_page(viewport={"width": 1000, "height": 420}, device_scale_factor=2)
        page.set_content(COVER)
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "cover.png"))
        made.append("cover.png")
        page.close()

        # 2-4. the console itself, from the static export
        page = b.new_page(viewport={"width": 1280, "height": 1000}, device_scale_factor=2)
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        page.wait_for_function("document.querySelectorAll('#divergenceChart .pt').length > 0")
        page.wait_for_timeout(600)

        page.locator(".finding").screenshot(path=str(out / "divergence.png"))
        made.append("divergence.png")

        page.evaluate("openCase('HHG-014')")
        page.wait_for_function("document.querySelectorAll('#graphSvg circle').length > 5")
        page.wait_for_timeout(800)
        page.locator(".case-head").screenshot(path=str(out / "case-head.png"))
        made.append("case-head.png")
        page.locator(".graph-frame").screenshot(path=str(out / "graph.png"))
        made.append("graph.png")
        b.close()

    server.shutdown()
    for name in made:
        kb = (out / name).stat().st_size / 1024
        print(f"  {name:18} {kb:6.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
