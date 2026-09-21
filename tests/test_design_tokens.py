"""The colour tokens, held to what docs/DESIGN.md says about them.

No browser needed: this reads frontend/styles.css and does the WCAG
arithmetic. It checks every text token against every surface a text token can
sit on, in both themes, and the two rules that make that matrix sufficient --
the dark theme is defined identically both places it is written, and no colour
reaches the page except through a token. What the page actually renders,
pairings and all, is measured in the browser by test_frontend.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
CSS = (FRONTEND / "styles.css").read_text(encoding="utf-8")

AA = 4.5

# Everything used as a text colour, and everything text is set on. The verdict
# washes are surfaces too: tags and stamps put muted text on them.
TEXT = ["ink", "ink-2", "ink-3", "accent", "accent-strong", "fraud", "legit", "uncertain"]
SURFACES = ["paper", "sheet", "wash", "accent-wash", "fraud-wash", "legit-wash", "uncertain-wash"]


def _block(selector: str) -> str:
    m = re.search(selector + r"\s*\{(.*?)\}", CSS, re.S)
    assert m, f"no token block matching {selector}"
    return m.group(1)


def _tokens(body: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"--([\w-]+):\s*([^;]+);", body)}


LIGHT_BLOCK = _block(r"(?m)^:root")
DARK_SYSTEM_BLOCK = _block(r':root:not\(\[data-theme="light"\]\)')
DARK_CHOSEN_BLOCK = _block(r':root\[data-theme="dark"\]')
THEMES = {"light": _tokens(LIGHT_BLOCK), "dark": _tokens(DARK_CHOSEN_BLOCK)}


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", hex_colour), f"not a #rrggbb colour: {hex_colour}"
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def _luminance(hex_colour: str) -> float:
    def lin(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_arithmetic_matches_the_wcag_reference_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)   # the classic AA grey


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("text", TEXT)
def test_every_text_token_meets_aa_on_every_surface(theme, text):
    t = THEMES[theme]
    low = {s: round(contrast(t[text], t[s]), 2) for s in SURFACES}
    failing = {s: r for s, r in low.items() if r < AA}
    assert not failing, f"--{text} in {theme}: {failing}"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("surface", ["accent", "accent-strong"])
def test_button_text_meets_aa_at_rest_and_on_hover(theme, surface):
    t = THEMES[theme]
    assert contrast(t["on-accent"], t[surface]) >= AA


@pytest.mark.parametrize("theme", THEMES)
def test_the_toast_inverts_ink_and_paper_legibly(theme):
    t = THEMES[theme]
    assert contrast(t["paper"], t["ink"]) >= AA


def test_the_dark_theme_is_the_same_in_both_places_it_is_defined():
    """Dark is written twice: once for the system setting, once for the
    toggle. If they drift, the theme depends on how you arrived at it."""
    assert _tokens(DARK_SYSTEM_BLOCK) == _tokens(DARK_CHOSEN_BLOCK)


def test_the_dark_theme_overrides_every_colour_the_light_theme_sets():
    """A colour token added to light and forgotten in dark renders a light
    colour on a dark page."""
    colourish = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(")
    light_colours = {k for k, v in THEMES["light"].items() if colourish.search(v)}
    missing = light_colours - set(THEMES["dark"])
    assert not missing, f"set in light, not in dark: {sorted(missing)}"


_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(|"
                      r":\s*(?:white|black|red|green|blue|gray|grey)\b", re.I)


def test_no_colour_reaches_the_page_except_through_a_token():
    """The token matrix only proves anything if nothing bypasses it."""
    rest = CSS
    for body in (LIGHT_BLOCK, DARK_SYSTEM_BLOCK, DARK_CHOSEN_BLOCK):
        rest = rest.replace(body, "")
    rest = re.sub(r"/\*.*?\*/", "", rest, flags=re.S)
    # the one deliberate exception: printing is on real white paper
    rest = rest.replace("body { background: #fff; }", "")
    offenders = [ln.strip() for ln in rest.splitlines() if _LITERAL.search(ln)]
    assert not offenders, offenders

    for name in ("app.js", "index.html"):
        src = re.sub(r"/\*.*?\*/", "", (FRONTEND / name).read_text(encoding="utf-8"), flags=re.S)
        hits = [ln.strip() for ln in src.splitlines()
                if re.search(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])|\brgba?\(", ln)]
        assert not hits, f"{name}: {hits}"
