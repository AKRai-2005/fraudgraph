"""The console, driven in a real browser.

These are the 29 checks that were first run by hand in the browser pane after
the redesign, made permanent. Each test names the behaviour it protects; the
number in its docstring is the check it replaces.

The console runs for real: the FastAPI app on a free port in a background
thread, pinned to the local mirror and no LLM so nothing here depends on a
Savanna workspace being awake or spends free-tier quota. State the console
writes -- approvals, re-run records, the audit log, the journal -- goes to the
scratch copies set up in conftest.py, so running these leaves the demo exactly
as it was.

Every page fails its test if it raises an uncaught exception or logs a
console error, so a check can not pass on a page that is quietly broken.

Run just these:  python -m pytest -m browser
"""
from __future__ import annotations

import functools
import json
import re
import socket
import threading
import time

import pytest

from fraudgraph.config import PATHS

playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not (PATHS.build / "tx_index.parquet").exists(),
                       reason="parquet cache not built"),
]

SLOW = 30_000   # ms: an investigation plus the live feed's reveal stagger


# ------------------------------------------------------------------ server
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(isolated_records):
    import uvicorn

    from fraudgraph.api import main as api_main
    from fraudgraph.api.service import CaseService

    service = CaseService(backend="local", use_llm=False)
    mp = pytest.MonkeyPatch()
    mp.setattr(api_main, "svc", functools.lru_cache(maxsize=1)(lambda: service))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(api_main.app, host="127.0.0.1", port=port,
                                        log_level="warning"))
    thread = threading.Thread(target=srv.run, name="console-under-test", daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not srv.started:
        if time.monotonic() > deadline or not thread.is_alive():
            mp.undo()
            pytest.fail("the console did not start within 60s")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        mp.undo()


@pytest.fixture(scope="module")
def browser():
    try:
        pw = playwright_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright could not start: {exc}")
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as exc:  # noqa: BLE001
        pw.stop()
        pytest.skip(f"no Chromium for Playwright: {exc} (python -m playwright install chromium)")
    try:
        yield b
    finally:
        b.close()
        pw.stop()


class Console:
    """One browser page on the console, with the few helpers the checks share."""

    def __init__(self, page, base: str):
        self.page, self.base = page, base
        self.errors: list[str] = []
        page.on("pageerror", lambda exc: self.errors.append(f"uncaught: {exc}"))
        page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

    def load(self) -> "Console":
        scored = len(self.page.request.get(self.base + "/api/divergence").json()["points"])
        self.page.goto(self.base)
        # the chart is the last thing boot() draws
        self.page.wait_for_function(
            "n => document.querySelectorAll('#divergenceChart .pt').length === n", arg=scored)
        return self

    def tab(self, name: str):
        return self.page.get_by_role("tab", name=name, exact=True)

    def view(self, key: str):
        return self.page.locator(f"#view-{key}")

    def rows(self):
        return self.page.locator("#queueTable tbody tr[data-case]")

    def heading(self):
        return self.page.locator(".case-id h1")

    def open_case(self, case_id: str) -> None:
        # setup only: the routes into a case are tested for real below
        self.page.evaluate("id => openCase(id)", case_id)
        expect(self.heading()).to_have_text(case_id)

    def record(self, case_id: str) -> dict:
        return self.page.request.get(f"{self.base}/api/cases/{case_id}").json()


def _new_console(browser, server, **ctx) -> tuple[Console, object]:
    opts = {"viewport": {"width": 1440, "height": 900}, "color_scheme": "light", **ctx}
    context = browser.new_context(**opts)
    return Console(context.new_page(), server), context


@pytest.fixture
def console(browser, server):
    c, context = _new_console(browser, server)
    c.load()
    yield c
    context.close()
    assert not c.errors, "the page reported errors:\n" + "\n".join(c.errors)


ACTIVE = re.compile(r"\bactive\b")


# ------------------------------------------------------------- navigation
def test_tabs_switch_views(console):
    """Check 1: every tab shows its own view and is marked selected."""
    for name, key in [("Overview", "overview"), ("Queue", "queue"), ("Case", "case"),
                      ("Memory", "memory"), ("Model & policy", "model")]:
        console.tab(name).click()
        expect(console.view(key)).to_have_class(ACTIVE)
        expect(console.tab(name)).to_have_attribute("aria-selected", "true")
    assert console.page.locator(".view.active").count() == 1


def test_arrow_keys_move_between_tabs(console):
    """Check 2: the tablist is keyboard-operable, as a tablist should be."""
    console.tab("Overview").focus()
    console.page.keyboard.press("ArrowRight")
    expect(console.view("queue")).to_have_class(ACTIVE)
    expect(console.tab("Queue")).to_be_focused()
    console.page.keyboard.press("ArrowLeft")
    expect(console.view("overview")).to_have_class(ACTIVE)


# ------------------------------------------------------------------ queue
def test_queue_search_filters_rows(console):
    """Check 3."""
    console.tab("Queue").click()
    console.page.fill("#queueSearch", "HHG-014")
    expect(console.rows()).to_have_count(1)
    expect(console.rows().first).to_have_attribute("data-case", "HHG-014")


def test_queue_verdict_filter_keeps_only_that_verdict(console):
    """Check 4, and the count says how many of how many are shown."""
    console.tab("Queue").click()
    total = console.rows().count()
    console.page.select_option("#queueVerdict", "legitimate")
    stamps = console.page.locator("#queueTable tbody .stamp").all_inner_texts()
    assert stamps and all(s.lower() == "legitimate" for s in stamps)
    expect(console.page.locator("#queueCount")).to_have_text(f"{len(stamps)} of {total} cases")


def test_queue_approval_filter_keeps_only_cases_awaiting_approval(console):
    """Check 5."""
    console.tab("Queue").click()
    console.page.check("#queueApproval")
    n = console.rows().count()
    assert n > 0, "no case is awaiting approval, so this checks nothing"
    assert console.page.locator("#queueTable tbody tr[data-case]:has(.tag.pending)").count() == n


def test_queue_sort_toggles_direction(console):
    """Check 6: sort state is announced, not just drawn."""
    console.tab("Queue").click()
    header = console.page.locator("#queueTable th[data-sort=bank_risk_score]")
    header.click()
    expect(header).to_have_attribute("aria-sort", "descending")
    header.click()
    expect(header).to_have_attribute("aria-sort", "ascending")


def test_queue_sort_actually_orders_the_rows(console):
    """Check 7: the arrow is not enough -- the rows must be in that order, both ways."""
    console.tab("Queue").click()
    header = console.page.locator("#queueTable th[data-sort=bank_risk_score]")

    def scores():
        cells = console.page.locator("#queueTable tbody tr[data-case] td:nth-child(4)").all_inner_texts()
        return [float(c) for c in cells if re.fullmatch(r"[0-9.]+", c.strip())]

    header.click()
    down = scores()
    assert len(set(down)) > 5, "too few distinct scores to tell an order from chance"
    assert down == sorted(down, reverse=True)
    header.click()
    assert scores() == sorted(down)


# ----------------------------------------------------- five ways into a case
def test_clicking_a_queue_row_opens_its_case(console):
    """Check 8: the whole row is a target for the mouse."""
    console.tab("Queue").click()
    row = console.rows().nth(2)
    case_id = row.get_attribute("data-case")
    row.locator("td").nth(2).click()           # the card cell, not the id button
    expect(console.view("case")).to_have_class(ACTIVE)
    expect(console.heading()).to_have_text(case_id)


def test_case_id_button_opens_the_case_from_the_keyboard(console):
    """Check 9: the row click is a convenience; the id is a real button."""
    console.tab("Queue").click()
    console.page.locator('#queueTable tr[data-case="HHG-006"] button').focus()
    console.page.keyboard.press("Enter")
    expect(console.heading()).to_have_text("HHG-006")


def test_chart_point_opens_its_case(console):
    """Check 10."""
    point = console.page.locator('#divergenceChart .pt[aria-label^="HHG-014:"] circle')
    point.click()
    expect(console.view("case")).to_have_class(ACTIVE)
    expect(console.heading()).to_have_text("HHG-014")


def test_margin_note_opens_its_case(console):
    """Check 11."""
    note = console.page.locator(".callout.down")
    case_id = note.get_attribute("data-case")
    note.click()
    expect(console.heading()).to_have_text(case_id)


def test_activity_log_case_id_opens_the_case(console):
    """Check 12: these were <span>s a keyboard could not reach."""
    button = console.page.locator("#recentActivity button[data-case]").first
    case_id = button.get_attribute("data-case")
    button.click()
    expect(console.view("case")).to_have_class(ACTIVE)
    expect(console.heading()).to_have_text(case_id)


# -------------------------------------------------------------- approvals
@pytest.fixture(scope="module")
def approval_flow(browser, server):
    """Refuse an unnamed approval, approve one action, reject another -- once."""
    c, context = _new_console(browser, server)
    c.load()
    c.open_case("HHG-014")
    page = c.page
    pending = page.locator(".appr-yes")
    before = pending.count()
    if before < 2:
        context.close()
        pytest.skip("HHG-014 needs two pending actions to approve one and reject one")
    first = pending.first.get_attribute("data-action")
    obs = {"pending_before": before, "first": first}

    pending.first.click()                                    # no name entered
    expect(page.locator("#toast")).to_contain_text("Enter your name")
    obs["pending_after_unnamed"] = page.locator(".appr-yes").count()

    page.fill(f'.appr-name[data-action="{first}"]', "ui-check")
    page.click(f'.appr-yes[data-action="{first}"]')
    approved = page.locator("#approvalList .tag.done-yes", has_text="ui-check")
    approved.wait_for(timeout=SLOW)
    obs["approved_tag"] = approved.inner_text()
    obs["execution"] = page.locator("#approvalList .act-exec").first.inner_text()

    reject = page.locator(".appr-no").last
    second = reject.get_attribute("data-action")
    page.fill(f'.appr-name[data-action="{second}"]', "ui-check")
    reject.click()
    rejected = page.locator("#approvalList .tag.done-no")
    rejected.wait_for(timeout=SLOW)
    obs["second"] = second
    obs["rejected_tag"] = rejected.inner_text()
    obs["server_record"] = c.record("HHG-014").get("approvals", {})
    obs["errors"] = list(c.errors)
    context.close()
    return obs


def test_an_approval_without_a_name_is_refused(approval_flow):
    """Check 13: an approval with no approver would be an unattributed decision."""
    assert approval_flow["pending_after_unnamed"] == approval_flow["pending_before"]
    assert not approval_flow["errors"], approval_flow["errors"]


def test_an_approval_is_recorded_by_the_server(approval_flow):
    """Check 14: shown on the page, and actually stored."""
    assert "approved by ui-check" in approval_flow["approved_tag"]
    stored = approval_flow["server_record"][approval_flow["first"]]
    assert stored["decision"] == "approved" and stored["approver"] == "ui-check"


def test_an_approval_shows_its_simulated_execution(approval_flow):
    """Check 15: execution is visibly simulated, never implied real."""
    assert "Simulated execution" in approval_flow["execution"]
    stored = approval_flow["server_record"][approval_flow["first"]]
    assert stored["execution"]["simulated"] is True


def test_a_rejection_is_recorded_and_executes_nothing(approval_flow):
    """Check 16."""
    assert "rejected by ui-check" in approval_flow["rejected_tag"]
    stored = approval_flow["server_record"][approval_flow["second"]]
    assert stored["decision"] == "rejected"
    assert "execution" not in stored


# ------------------------------------------------------------------ re-runs
def test_a_quiet_rerun_says_it_is_a_rerun(console):
    """Check 17: a re-run is labelled as a working copy, not the published answer."""
    console.open_case("HHG-003")
    expect(console.page.locator(".provenance")).to_contain_text("Published answer file")
    console.page.click("#rerunBtn")
    expect(console.page.locator(".provenance")).to_contain_text("Live re-run", timeout=SLOW)
    expect(console.page.locator(".provenance")).to_contain_text("unchanged")


@pytest.fixture(scope="module")
def watch_flow(browser, server):
    """Stream one investigation and record what the live panel did."""
    c, context = _new_console(browser, server)
    c.load()
    c.open_case("HHG-014")
    page = c.page
    obs = {}
    page.click("#watchBtn")
    try:
        expect(page.locator("#livePanel")).to_be_visible(timeout=3_000)
        obs["panel_opened"] = True
    except AssertionError:
        obs["panel_opened"] = False
    obs["disabled_while_running"] = page.locator("#watchBtn").is_disabled()
    page.wait_for_function("document.querySelectorAll('#liveFeed .lf.q').length > 3", timeout=SLOW)
    # seen while the verdict is still pending: the lines arrive as the agent works
    obs["arrived_before_verdict"] = page.locator("#liveState").inner_text() == "running"
    page.wait_for_function(r"/p=[01]\.\d\d/.test(document.querySelector('#liveState').textContent)",
                           timeout=SLOW)
    obs["queries_streamed"] = page.locator("#liveFeed .lf.q").count()
    obs["state"] = page.locator("#liveState").inner_text()
    obs["reenabled"] = page.locator("#watchBtn").is_enabled()
    obs["record"] = c.record("HHG-014")
    obs["errors"] = list(c.errors)
    context.close()
    return obs


def test_watching_opens_the_live_panel(watch_flow):
    """Check 18."""
    assert watch_flow["panel_opened"]
    assert watch_flow["disabled_while_running"], "the button could start a second stream"


def test_the_live_feed_streams_graph_queries(watch_flow):
    """Check 19: the feed shows the queries as the agent runs them -- every one
    of them, the same calls the finished record logs."""
    assert watch_flow["arrived_before_verdict"]
    assert watch_flow["queries_streamed"] == len(watch_flow["record"]["tool_log"])
    assert not watch_flow["errors"], watch_flow["errors"]


def test_the_live_panel_finishes_on_the_verdict_and_real_latency(watch_flow):
    """Check 20. The seconds shown must be the agent's own latency: an earlier
    version showed the browser's wall clock, which the reveal stagger inflated
    from about 1.2s to 7.7s."""
    answer = watch_flow["record"]["answer"]
    verdict, p = answer["case"]["verdict"], answer["case"]["fraud_probability"]
    assert watch_flow["state"].startswith(verdict)
    assert f"p={p:.2f}" in watch_flow["state"]
    assert watch_flow["state"].endswith(f"{answer['latency_s']:.2f}s")


def test_the_watch_button_is_usable_again_afterwards(watch_flow):
    """Check 21."""
    assert watch_flow["reenabled"]


# ------------------------------------------------------------------- graph
def test_the_case_graph_is_drawn(console):
    """Check 22."""
    console.open_case("HHG-014")
    console.page.wait_for_function("document.querySelectorAll('#graphSvg circle').length > 5")
    expect(console.page.locator("#graphMeta")).to_contain_text("nodes")


def test_the_graph_pans_and_resets(console):
    """Check 23, with a real drag: panning moved to pointer events so touch works."""
    console.open_case("HHG-014")
    console.page.wait_for_function("document.querySelectorAll('#graphSvg circle').length > 5")
    svg = console.page.locator("#graphSvg")
    svg.scroll_into_view_if_needed()
    box = svg.bounding_box()
    x, y = box["x"] + 12, box["y"] + box["height"] - 12       # empty corner, not a node
    console.page.mouse.move(x, y)
    console.page.mouse.down()
    console.page.mouse.move(x + 60, y - 40, steps=6)
    console.page.mouse.up()
    root = console.page.locator("#gRoot")
    moved = root.get_attribute("transform")
    assert moved and not moved.startswith("translate(0,0)"), f"the drag did not pan: {moved}"
    console.page.click("#graphReset")
    expect(root).to_have_attribute("transform", "translate(0,0) scale(1)")


# ------------------------------------------------------------------- ad hoc
def test_ad_hoc_investigation_refuses_a_non_numeric_id(console):
    """Check 24: validated before anything is sent."""
    console.tab("Queue").click()
    console.page.fill("#adhocTxn", "abc")
    console.page.click("#adhocBtn")
    expect(console.page.locator("#adhocMsg")).to_contain_text("numeric")
    expect(console.view("queue")).to_have_class(ACTIVE)


def test_ad_hoc_investigation_opens_the_new_case(console):
    """Check 25."""
    console.tab("Queue").click()
    console.page.fill("#adhocTxn", "3514030")
    console.page.click("#adhocBtn")
    expect(console.heading()).to_have_text("ADHOC-3514030", timeout=SLOW)
    expect(console.view("case")).to_have_class(ACTIVE)


# ------------------------------------------------------------ other views
def test_memory_view_loads(console):
    """Check 26: both ledgers and the case table, with no error box."""
    console.tab("Memory").click()
    expect(console.page.locator("#memoryBody .ledger")).to_have_count(2)
    rows = console.page.locator("#memoryBody tbody tr")
    expect(rows.first).to_be_visible()
    assert rows.count() > 5
    expect(console.page.locator("#view-memory .errorbox")).to_have_count(0)


def test_model_view_loads(console):
    """Check 27: backtest, weights, logistic fit and policy tables."""
    console.tab("Model & policy").click()
    console.page.wait_for_function("document.querySelectorAll('#modelBody table').length >= 4")
    expect(console.page.locator("#modelBody h2", has_text="Are the verdicts right?")).to_be_visible()
    expect(console.page.locator("#view-model .errorbox")).to_have_count(0)


def test_theme_toggle_switches_and_persists(console):
    """Check 28, plus what was claimed about it: the choice survives a reload."""
    page = console.page
    html = page.locator("html")
    page.click("#themeToggle")
    expect(html).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('fg-theme')") == "dark"
    expect(page.locator("#themeToggle")).to_have_attribute("aria-label", "Switch to light theme")
    console.load()
    expect(html).to_have_attribute("data-theme", "dark")
    page.click("#themeToggle")
    expect(html).to_have_attribute("data-theme", "light")


def test_no_view_shows_an_error_state(console):
    """Check 29: a tour of every view with no error box. The fixture adds the
    rest: no uncaught exception and no console error on the way."""
    for name in ("Overview", "Queue", "Memory", "Model & policy"):
        console.tab(name).click()
        console.page.wait_for_timeout(600)
    console.open_case("HHG-014")
    expect(console.page.locator(".errorbox")).to_have_count(0)


# ------------------------------------------------------ layout, every width
# Not one of the 29, but the same pass checked these by hand and the design
# notes claim them, so they are held to it here.
WIDTHS = [320, 375, 414, 768, 1024, 1280, 1440, 1920]

_OVERFLOW = """() => {
  const vw = document.documentElement.clientWidth;
  const page = document.documentElement.scrollWidth - vw;
  const sec = document.querySelector('.view.active');
  const out = [...sec.querySelectorAll('*')].filter((n) => {
    const r = n.getBoundingClientRect();
    if (r.width === 0 || r.right <= vw + 1) return false;
    for (let p = n.parentElement; p && p !== sec; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o === 'auto' || o === 'scroll' || o === 'hidden') return false;
    }
    return true;
  }).slice(0, 3).map((n) => n.tagName.toLowerCase() + '.' + String(n.className).split(' ')[0]);
  return { page, out };
}"""


@pytest.fixture(scope="module")
def layout_page(browser, server):
    c, context = _new_console(browser, server)
    c.load()
    yield c
    context.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_no_view_scrolls_sideways(layout_page, width):
    c = layout_page
    c.page.set_viewport_size({"width": width, "height": 900})
    c.page.wait_for_timeout(250)                       # the chart redraws on resize
    for key in ("overview", "queue", "memory", "model"):
        c.page.evaluate("v => show(v)", key)
        c.page.wait_for_timeout(700 if key in ("memory", "model") else 250)
        res = c.page.evaluate(_OVERFLOW)
        assert res["page"] == 0 and not res["out"], f"{key} at {width}px: {res}"
    c.open_case("HHG-014")
    res = c.page.evaluate(_OVERFLOW)
    assert res["page"] == 0 and not res["out"], f"case at {width}px: {res}"
    if width >= 1024:
        c.page.evaluate("show('queue')")
        spill = c.page.evaluate("""() => { const t = document.querySelector('#queueTable');
            return t.scrollWidth - t.closest('.table-wrap').clientWidth; }""")
        assert spill <= 0, f"the queue table scrolls sideways at {width}px by {spill}px"
    assert not c.errors, c.errors


@pytest.mark.parametrize("width", [320, 1440])
def test_simulated_actions_are_always_visible(layout_page, width):
    """The honesty signal in the status line may never be the thing that is cut off."""
    c = layout_page
    c.page.set_viewport_size({"width": width, "height": 900})
    item = c.page.locator("#statusStrip .st").first
    expect(item).to_contain_text("simulated")
    box, strip = item.bounding_box(), c.page.locator("#statusStrip").bounding_box()
    assert box["x"] >= strip["x"] - 1 and box["x"] + box["width"] <= strip["x"] + strip["width"] + 1


@pytest.mark.parametrize("width", [1024, 1440])
def test_the_header_stays_on_screen_while_a_case_scrolls(layout_page, width):
    """Above 960px the header is sticky, and with it the status line. It once
    scrolled away: overflow-x: hidden on html and body made body a scroll
    container that never scrolls, so the header stuck to nothing. (At 960 and
    below it is deliberately static: three rows would eat a phone screen.)"""
    c = layout_page
    c.page.set_viewport_size({"width": width, "height": 800})
    c.open_case("HHG-014")
    c.page.evaluate("window.scrollTo(0, 1500)")
    assert c.page.evaluate("scrollY") > 1000, "the case is too short to test scrolling"
    header = c.page.locator(".app-header").bounding_box()
    assert abs(header["y"]) < 1, f"the header is at y={header['y']} after scrolling"
    expect(c.page.locator("#statusStrip .st").first).to_be_in_viewport()
    c.page.evaluate("window.scrollTo(0, 0)")


# --------------------------------------------------- contrast, as rendered
# test_design_tokens.py proves every text token clears AA on every surface
# token. This measures what the page actually draws: each visible run of text
# -- HTML, SVG labels, form values, placeholders -- against the background
# really behind it, found by compositing the background colours of its
# ancestors, with element opacity applied to the text. It catches pairings the
# matrix does not list and anything that reaches the page without a token.
#
# What it does not see: text over a positioned sibling rather than an ancestor
# (the console has none -- bars sit beside their figures, tooltips carry their
# own background), and hover states beyond the two it forces; those use only
# tokens the matrix covers. Disabled controls are exempt, as WCAG exempts them.

_CONTRAST = r"""(root) => {
  const scope = root ? document.querySelector(root) : document.body;
  if (!scope) return { missing: root };
  const rgba = (s) => {
    const m = /rgba?\(([^)]+)\)/.exec(s || '');
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top, under) =>
    [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat(1);
  const lum = (c) => {
    const v = c.slice(0, 3).map((x) => { x /= 255; return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const backdrop = (el) => {
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage !== 'none') return null;
      const c = rgba(cs.backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    return layers.reduceRight((under, top) => over(top, under), [255, 255, 255, 1]);
  };
  const opacity = (el) => {
    let o = 1;
    for (let n = el; n; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity);
    return o;
  };
  const cls = (el) => (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).join('.');
  const name = (el, text) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
    + (cls(el) ? '.' + cls(el) : '') + ' "' + text.trim().replace(/\s+/g, ' ').slice(0, 40) + '"';
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    return r.width >= 2 && r.height >= 2 && el.checkVisibility({ visibilityProperty: true });
  };

  const res = { checked: 0, failures: [], unknown: [], min: 99, minAt: '' };
  const measure = (el, colour, text, extraAlpha) => {
    const fg0 = rgba(colour);
    if (!fg0) { res.unknown.push('colour ' + colour + ' on ' + name(el, text)); return; }
    const bg = backdrop(el);
    if (!bg) { res.unknown.push('background image behind ' + name(el, text)); return; }
    const fg = over([fg0[0], fg0[1], fg0[2], fg0[3] * extraAlpha * opacity(el)], bg);
    const cs = getComputedStyle(el);
    const px = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
    const need = px >= 24 || (px >= 18.66 && weight >= 700) ? 3 : 4.5;
    const r = ratio(fg, bg);
    res.checked += 1;
    if (r < res.min) { res.min = r; res.minAt = name(el, text); }
    if (r < need) res.failures.push(`${name(el, text)}: ${r.toFixed(2)} < ${need}`);
  };

  const all = [scope, ...scope.querySelectorAll('*')];
  for (const el of all) {
    if (el.closest(':disabled') || !shown(el)) continue;
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('');
    if (el instanceof SVGElement) {
      if (text.trim()) {
        const cs = getComputedStyle(el);
        if (cs.fill !== 'none') measure(el, cs.fill, text, parseFloat(cs.fillOpacity));
      }
      continue;
    }
    if (el.matches('input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea')) {
      if (el.value) measure(el, getComputedStyle(el).color, el.value, 1);
      if (el.placeholder) measure(el, getComputedStyle(el, '::placeholder').color, el.placeholder, 1);
      continue;
    }
    if (el.matches('option')) continue;       // drawn by the platform's own menu
    if (text.trim()) measure(el, getComputedStyle(el).color, text, 1);
  }
  return res;
}"""


class Contrast:
    def __init__(self, page):
        self.page, self.checked, self.failures, self.unknown = page, 0, [], []
        self.min, self.min_at = 99.0, ""

    def measure(self, state: str, root: str | None = None) -> None:
        r = self.page.evaluate(_CONTRAST, root)
        assert "missing" not in r, f"{state}: nothing matches {root}"
        assert r["checked"] > 0, f"{state}: no text measured under {root}"
        self.checked += r["checked"]
        self.failures += [f"[{state}] {f}" for f in r["failures"]]
        self.unknown += [f"[{state}] {u}" for u in r["unknown"]]
        if r["min"] < self.min:
            self.min, self.min_at = r["min"], f"[{state}] {r['minAt']}"


@pytest.mark.parametrize("width", [1440, 375])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_all_rendered_text_meets_aa(browser, server, theme, width):
    """Every state a person can reach without breaking anything, plus two that
    are hard to reach on purpose: an error box and a drifted re-run."""
    c, context = _new_console(browser, server, color_scheme=theme, reduced_motion="reduce",
                              viewport={"width": width, "height": 900})
    page, m = c.page, Contrast(c.page)
    try:
        c.load()
        assert page.evaluate("currentTheme()") == theme
        m.measure("overview")
        page.locator("#divergenceChart .pt").first.focus()
        m.measure("chart tooltip", "#divergenceTip")

        c.tab("Queue").click()
        m.measure("queue")
        page.locator("#queueTable tbody tr[data-case]").first.hover()
        m.measure("queue row, hovered", "#queueTable tbody tr[data-case]")
        page.fill("#adhocTxn", "abc")
        page.click("#adhocBtn")
        m.measure("ad hoc refusal", "#adhocMsg")

        # a case with an approval still open, so the Approve button is there to
        # measure -- the approval tests above may have used up HHG-014's
        queue = page.request.get(server + "/api/queue").json()
        pending = [r["case_id"] for r in queue if r.get("awaiting_approval")]
        assert pending, "no case awaits approval, so the Approve button cannot be measured"
        c.open_case("HHG-014" if "HHG-014" in pending else pending[0])
        page.wait_for_function("document.querySelectorAll('#graphSvg circle').length > 5")
        page.evaluate("document.querySelectorAll('#caseBody details').forEach((d) => { d.open = true; })")
        m.measure("case, every section open")
        page.locator("#graphSvg circle").first.dispatch_event("pointerenter")
        m.measure("graph tooltip", "#graphTip")
        page.locator(".appr-yes").first.hover()
        m.measure("approve button, hovered", ".appr-yes")
        page.locator(".appr-yes").first.click()          # no name: refused with a toast
        m.measure("toast", "#toast")

        page.click("#watchBtn")
        page.wait_for_function(r"/p=[01]\.\d\d/.test(document.querySelector('#liveState').textContent)",
                               timeout=SLOW)
        m.measure("live investigation", "#livePanel")

        c.tab("Memory").click()
        expect(page.locator("#memoryBody .ledger")).to_have_count(2)
        m.measure("memory")
        c.tab("Model & policy").click()
        page.wait_for_function("document.querySelectorAll('#modelBody table').length >= 4")
        m.measure("model")

        assert not c.errors, c.errors

        # A drifted re-run is hard to produce on demand, so the browser is handed
        # one: the real record with its provenance marked as a re-run whose facts
        # differ. Only the rendering is under test here.
        def drifted(route):
            rec = route.fetch().json()
            rec["provenance"] = {"kind": "rerun", "backend": "local", "at": rec.get("generated_at")}
            rec["drift"] = {"facts_match": False, "n_differences": 1, "differences": [
                {"path": "case.verdict", "published": "fraud", "current": "uncertain"}]}
            route.fulfill(json=rec)
        page.route("**/api/cases/HHG-003", drifted)
        c.open_case("HHG-003")
        expect(page.locator(".provenance.drifted")).to_be_visible()
        m.measure("drifted re-run", ".provenance")
        page.unroute("**/api/cases/HHG-003")

        page.route("**/api/memory", lambda route: route.fulfill(
            status=500, json={"detail": "forced by the contrast test"}))
        c.tab("Memory").click()
        expect(page.locator("#view-memory .errorbox")).to_be_visible()
        m.measure("error box", "#view-memory .errorbox")
    finally:
        context.close()

    assert not m.unknown, "could not measure:\n" + "\n".join(m.unknown)
    assert m.checked > 400, f"only {m.checked} runs of text measured; the tour is not reaching the page"
    assert not m.failures, (f"{len(m.failures)} of {m.checked} runs of text below AA "
                            f"({theme}, {width}px):\n" + "\n".join(m.failures[:40]))
    print(f"\n{theme} {width}px: {m.checked} runs of text, lowest {m.min:.2f}:1 at {m.min_at}")
