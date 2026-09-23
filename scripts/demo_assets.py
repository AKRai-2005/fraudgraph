"""Render the demo video's cards and overlays as PNGs.

Everything uses the console's own tokens, so the cards and the application
footage look like one piece. No watermarks, no branding that is not the
project's own.

    python scripts/demo_assets.py        # -> demo/assets/*.png

1920x1080 cards; the lower thirds are transparent PNGs to sit over footage.
"""
from __future__ import annotations

import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "demo" / "assets"

BASE = """
:root { --paper:#f6f4ee; --sheet:#fffdf8; --ink:#1b1a17; --ink-2:#4d4a43; --ink-3:#66625a;
        --rule:#dcd7cc; --fraud:#a3261b; --legit:#2c6639; --uncertain:#855606; --accent:#274690; }
* { box-sizing: border-box; margin: 0; }
body { width: 1920px; height: 1080px; background: var(--paper); color: var(--ink);
       font: 28px/1.5 system-ui, "Segoe UI", sans-serif; padding: 96px 120px;
       display: flex; flex-direction: column; justify-content: space-between; }
.eyebrow { font-size: 22px; letter-spacing: .18em; text-transform: uppercase;
           color: var(--ink-3); font-weight: 600; }
h1 { font: 700 96px/1.06 Charter, Cambria, Georgia, serif; letter-spacing: -.015em;
     margin: 28px 0 24px; max-width: 24ch; }
.sub { font: italic 36px/1.4 Charter, Cambria, Georgia, serif; color: var(--ink-2); max-width: 46ch; }
.figs { display: flex; border-top: 2px solid var(--ink); padding-top: 28px; }
.figs div { padding-right: 52px; margin-right: 52px; border-right: 1px solid var(--rule); }
.figs div:last-child { border-right: 0; }
.figs b { display: block; font: 700 52px/1 Charter, Cambria, Georgia, serif; }
.figs b.up { color: var(--legit); } .figs b.no { color: var(--fraud); }
.figs span { font-size: 21px; color: var(--ink-3); }
.foot { display: flex; justify-content: space-between; font-size: 24px; color: var(--ink-3); }
.foot b { color: var(--accent); font-weight: 600; }
"""

SECTION = BASE + """
body { justify-content: center; }
.num { font: 700 32px/1 Consolas, monospace; color: var(--ink-3); letter-spacing: .1em; }
h1 { font-size: 104px; margin: 24px 0 0; max-width: 22ch; }
.rule { width: 220px; height: 5px; background: var(--ink); margin: 40px 0 0; }
.note { font: 30px/1.5 system-ui, sans-serif; color: var(--ink-2); margin-top: 34px; max-width: 40ch; }
"""

LOWER = """
* { box-sizing: border-box; margin: 0; }
body { width: 1920px; height: 220px; background: transparent;
       font: 30px/1.4 system-ui, "Segoe UI", sans-serif; display: flex; align-items: flex-end;
       padding: 0 0 40px 120px; }
.bar { background: #1b1a17f2; color: #f6f4ee; padding: 20px 32px; border-radius: 4px;
       border-left: 6px solid var(--c, #a3261b); max-width: 1400px; }
.k { font: 700 34px/1.2 Charter, Cambria, Georgia, serif; }
.v { font-size: 25px; color: #cfcabf; margin-top: 6px; }
.v b { color: #fff; font-weight: 600; }
"""

CARDS: dict[str, tuple[str, str]] = {
    "01-title": (BASE, """
<div>
  <p class="eyebrow">TigerGraph &times; Hacker House Goa 2026</p>
  <h1>Investigating fraud on a graph, not scoring it</h1>
  <p class="sub">An agent that takes a bank alert, investigates it across 590,742
     transactions, and says what to do next &mdash; with the query behind every claim.</p>
</div>
<div class="figs">
  <div><b>590,742</b><span>transactions in the graph</span></div>
  <div><b>18 of 20</b><span>alerts the graph moved</span></div>
  <div><b class="up">9 of 9</b><span>undocumented frauds caught in replay</span></div>
  <div><b class="no">0</b><span>false fraud calls on 300 hard negatives</span></div>
</div>
<div class="foot"><span>github.com/<b>AKRai-2005/fraudgraph</b></span>
  <span>TigerGraph Savanna &middot; GSQL &middot; MCP &middot; Gemini</span></div>"""),

    "02-section-disagreement": (SECTION, """
<div><p class="num">01</p><h1>Where the graph disagrees with the score</h1>
<div class="rule"></div>
<p class="note">Twenty alerts. The bank's model on one axis, the agent's assessment on the other.</p></div>"""),

    "03-section-live": (SECTION, """
<div><p class="num">02</p><h1>Watch it investigate</h1>
<div class="rule"></div>
<p class="note">Not a replay. Every reasoning step and graph query as it happens.</p></div>"""),

    "04-section-policy": (SECTION, """
<div><p class="num">03</p><h1>Policy, and who has to approve</h1>
<div class="rule"></div>
<p class="note">Every action cites its rule. L1 and L2 wait for a named person. Execution is simulated.</p></div>"""),

    "05-section-measured": (SECTION, """
<div><p class="num">04</p><h1>Does it actually work?</h1>
<div class="rule"></div>
<p class="note">No accuracy is claimed on the twenty. Measured instead on 5,565 closed cases with known outcomes.</p></div>"""),

    "06-end": (BASE, """
<div>
  <p class="eyebrow">Everything in this demo is reproducible</p>
  <h1>Run it yourself</h1>
  <p class="sub">The repository runs with no credentials at all &mdash; the local mirror
     serves the same query catalogue.</p>
</div>
<div class="figs">
  <div><b>310</b><span>automated tests, 45 in a browser</span></div>
  <div><b>20 / 20</b><span>answer files validated</span></div>
  <div><b>&#8377;0</b><span>infrastructure, free tiers only</span></div>
</div>
<div class="foot"><span>github.com/<b>AKRai-2005/fraudgraph</b></span>
  <span>akrai-2005.github.io/fraudgraph</span></div>"""),
}

LOWER_THIRDS: dict[str, str] = {
    "lt-simulated": """<div class="bar" style="--c:#855606">
      <div class="k">Every action here is simulated</div>
      <div class="v">No card is blocked, no report is filed. L1 and L2 actions wait for a named approver.</div></div>""",
    "lt-hhg014": """<div class="bar" style="--c:#a3261b">
      <div class="k">HHG-014 &nbsp;&middot;&nbsp; bank score 0.05 &rarr; agent 0.98</div>
      <div class="v">One device profile, <b>28 unrelated cards</b> in a month &mdash; one profile out of 9,706 meets that test.</div></div>""",
    "lt-hhg007": """<div class="bar" style="--c:#2c6639">
      <div class="k">HHG-007 &nbsp;&middot;&nbsp; bank score 0.87 &rarr; agent 0.02</div>
      <div class="v">Cleared. The simulated customer reply removed step-up auth and the cardholder call.</div></div>""",
    "lt-backtest": """<div class="bar" style="--c:#2c6639">
      <div class="k">9 of 9 undocumented-typology frauds, from graph structure alone</div>
      <div class="v">Neutral prior, every window clamped at the alert. <b>0 false fraud calls</b> at the exam's operating point.</div></div>""",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    made = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        for name, (css, body) in CARDS.items():
            page.set_content(f"<style>{css}</style>{body}")
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / f"{name}.png"))
            made.append(f"{name}.png")
        page.close()

        page = b.new_page(viewport={"width": 1920, "height": 220})
        for name, body in LOWER_THIRDS.items():
            page.set_content(f"<style>{LOWER}</style>{body}")
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / f"{name}.png"), omit_background=True)
            made.append(f"{name}.png")
        b.close()

    for name in made:
        print(f"  {name:28} {(out / name).stat().st_size / 1024:6.0f} KB")
    print(f"\n{len(made)} assets -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
