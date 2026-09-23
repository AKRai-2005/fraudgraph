"""Record reference footage of the console, scene by scene.

This drives the real application -- nothing is mocked -- against an isolated
copy of the data, so the approvals and re-runs it performs never touch the
demo state. Output is silent 1080p B-roll you can narrate over, or use as a
reference for your own screen recording.

    python scripts/demo_record.py              # -> demo/reference/clips/*.mp4
    python scripts/demo_record.py --assemble   # and one cut with the cards

Two things the reference cannot show, and your own recording should:
  * the mouse cursor (headless Chromium does not draw one, so a synthetic
    pointer is drawn instead);
  * the LLM narration step, which is switched off here for pacing.
"""
from __future__ import annotations

import argparse
import functools
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "demo" / "reference"
CLIPS = OUT / "clips"
ASSETS = REPO / "demo" / "assets"
W, H = 1920, 1080

# A pointer, because headless Chromium records no cursor. Self-healing: an
# element injected at document_start is discarded when the real document
# parses, so each call re-creates it if it has gone.
CURSOR = """(x, y, click) => {
  let d = document.getElementById('__cur');
  if (!d) {
    d = document.createElement('div');
    d.id = '__cur';
    d.style.cssText = 'position:fixed;z-index:99999;width:22px;height:22px;margin:-11px 0 0 -11px;'
      + 'border-radius:50%;border:2px solid #1b1a17;background:rgba(27,26,23,.18);pointer-events:none;'
      + 'transition:left .45s cubic-bezier(.3,.7,.3,1),top .45s cubic-bezier(.3,.7,.3,1),transform .12s;'
      + 'left:-50px;top:-50px;';
    (document.body || document.documentElement).appendChild(d);
  }
  if (x != null) { d.style.left = x + 'px'; d.style.top = y + 'px'; }
  if (click) { d.style.transform = 'scale(.6)'; setTimeout(() => { d.style.transform = 'scale(1)'; }, 140); }
}"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(state: Path) -> tuple[str, object, object]:
    """The real API, on an isolated copy of the data."""
    import os

    os.environ["FG_BUILD_DIR"] = str(state)
    os.environ["FG_RECORDS_DIR"] = str(state / "case_records")
    os.environ["FG_GRAPH_BACKEND"] = "local"
    os.environ["FG_LLM_PROVIDER"] = "none"
    import uvicorn

    from fraudgraph.api.main import app

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 90
    while not server.started:
        if time.monotonic() > deadline:
            raise SystemExit("the console did not start")
        time.sleep(0.1)
    return f"http://127.0.0.1:{port}", server, thread


class Scene:
    """One recorded clip: its own browser context, so its own video file."""

    def __init__(self, browser, base: str, name: str):
        self.name = name
        self.ctx = browser.new_context(viewport={"width": W, "height": H},
                                       record_video_dir=str(CLIPS / "raw"),
                                       record_video_size={"width": W, "height": H},
                                       color_scheme="light", reduced_motion="no-preference")
        self.page = self.ctx.new_page()
        self.base = base

    def open(self, path: str = "/") -> None:
        self.page.goto(self.base + path)
        self.page.wait_for_function(
            "document.querySelectorAll('#divergenceChart .pt').length > 0", timeout=30000)
        self.page.wait_for_timeout(900)

    def point(self, selector: str, hold: int = 700) -> None:
        """Move the drawn pointer onto an element, as a presenter would."""
        box = self.page.locator(selector).first.bounding_box()
        if not box:
            return
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.evaluate(CURSOR, [x, y, False])
        self.page.mouse.move(x, y, steps=12)
        self.page.wait_for_timeout(hold)

    def click(self, selector: str, hold: int = 800) -> None:
        self.point(selector, 420)
        self.page.evaluate(CURSOR, [None, None, True])
        self.page.locator(selector).first.click()
        self.page.wait_for_timeout(hold)

    def glide(self, selector: str, ms: int = 2600) -> None:
        """Scroll an element into view slowly enough to read. A selector that
        matches nothing holds the shot instead of failing the recording."""
        target = self.page.locator(selector).first
        if target.count():
            target.evaluate("el => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
        self.page.wait_for_timeout(ms)

    def hold(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def close(self) -> Path:
        video = self.page.video
        self.ctx.close()                       # flushes the file
        raw = Path(video.path())
        target = CLIPS / "raw" / f"{self.name}.webm"
        raw.replace(target)
        return target


# ------------------------------------------------------------------ scenes
def scene_chart(s: Scene) -> None:                                   # ~35s
    s.open()
    s.hold(3500)
    s.point("#divergenceStats", 2500)
    s.point('#divergenceChart .pt[aria-label^="HHG-014:"] circle', 3200)
    s.point('#divergenceChart .pt[aria-label^="HHG-007:"] circle', 3200)
    s.point("#statusStrip .st", 2600)
    s.glide(".finding", 2200)
    s.point(".callout.up", 3000)
    s.point(".callout.down", 3000)
    s.hold(6000)


def scene_live(s: Scene) -> None:                                    # ~40s
    s.open()
    s.click('#divergenceChart .pt[aria-label^="HHG-014:"] circle', 2500)
    s.click("#watchBtn", 1200)
    s.page.wait_for_function(
        r"/p=[01]\.\d\d|failed/.test(document.querySelector('#liveState').textContent)",
        timeout=60000)
    s.hold(2500)
    feed = "#liveFeed"
    for frac in (0.15, 0.35, 0.55, 0.8, 1.0):                        # read down the feed
        s.page.locator(feed).evaluate(
            "(el, f) => el.scrollTo({top: el.scrollHeight * f, behavior: 'smooth'})", frac)
        s.hold(3200)
    s.point("#liveState", 3000)
    s.hold(4000)


def scene_evidence(s: Scene) -> None:                                # ~45s
    s.open()
    s.page.evaluate("openCase('HHG-014')")
    s.page.wait_for_function("document.querySelectorAll('#graphSvg circle').length > 5",
                             timeout=30000)
    s.hold(2500)
    s.glide(".case-head .ledger", 3000)
    s.glide(".ev-list .ev:nth-child(5)", 3000)
    s.hold(3500)
    s.glide(".alert", 3500)
    s.glide(".graph-frame", 3000)
    s.hold(3000)
    s.point("#graphSvg circle", 2500)
    s.hold(5000)
    s.glide(".detectors", 3500)
    s.hold(3000)


def scene_policy(s: Scene) -> None:                                  # ~35s
    s.open()
    s.page.evaluate("openCase('HHG-014')")
    s.page.wait_for_function("document.querySelectorAll('#graphSvg circle').length > 5",
                             timeout=30000)
    s.glide("#approvalList", 2600)
    s.hold(2500)
    s.point("#approvalList .act-row .tag.route", 2200)
    name = s.page.locator(".appr-name").first
    if name.count():
        s.point(".appr-name", 500)
        name.click()
        name.type("A. Rai", delay=180)
        s.hold(900)
        s.click(".appr-yes", 2600)
        s.glide("#approvalList .act-exec", 2600)
        s.hold(4000)
    s.glide("#caseBody h2:has-text('Suspicious activity report')", 2600)
    s.hold(4500)


def scene_changed(s: Scene) -> None:                                 # ~22s
    s.open()
    s.click('#divergenceChart .pt[aria-label^="HHG-007:"] circle', 2500)
    s.hold(2000)
    s.glide(".case-head .ledger", 2600)
    s.glide(".initial-list", 3000)
    s.hold(3500)
    s.glide(".changed", 2600)
    s.hold(5000)


def scene_measured(s: Scene) -> None:                                # ~40s
    s.open()
    s.click("#tab-model", 2500)
    s.page.wait_for_function("document.querySelectorAll('#modelBody table').length >= 4",
                             timeout=30000)
    s.hold(2500)
    for sel, ms in (("#modelBody h2", 2600), ("#modelBody table >> nth=0", 4000),
                    ("#modelBody table >> nth=1", 4000)):
        s.glide(sel, ms)
    s.page.evaluate("window.scrollBy({top: 500, behavior: 'smooth'})")
    s.hold(4000)
    s.page.evaluate("window.scrollBy({top: 500, behavior: 'smooth'})")
    s.hold(4000)
    s.hold(6000)


SCENES = [("02-chart", scene_chart), ("03-live", scene_live), ("04-evidence", scene_evidence),
          ("05-policy", scene_policy), ("06-changed", scene_changed), ("07-measured", scene_measured)]

# the assembled cut: (source, seconds). Cards are stills; clips run their length.
CUT = [("card:01-title", 12), ("card:02-section-disagreement", 4), ("clip:02-chart", None),
       ("card:03-section-live", 4), ("clip:03-live", None), ("clip:04-evidence", None),
       ("card:04-section-policy", 4), ("clip:05-policy", None), ("clip:06-changed", None),
       ("card:05-section-measured", 4), ("clip:07-measured", None), ("card:06-end", 10)]


def to_mp4(src: Path, dst: Path, seconds: float | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if src.suffix == ".png":
        cmd += ["-loop", "1", "-t", str(seconds or 5), "-i", str(src)]
    else:
        cmd += ["-i", str(src)]
    cmd += ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                   f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0xf6f4ee,fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-an", str(dst)]
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assemble", action="store_true", help="also build one reference cut")
    ap.add_argument("--only", help="record a single scene by name")
    args = ap.parse_args(argv)

    bundle = REPO / "build" / "deploy_bundle"
    if not (bundle / "tx_index.parquet").exists():
        print("Run scripts/make_deploy_bundle.py first (it assembles the data this needs).")
        return 2
    state = REPO / "build" / "demo_state"
    if state.exists():
        shutil.rmtree(state)
    shutil.copytree(bundle, state)              # isolated: the demo writes only here

    CLIPS.mkdir(parents=True, exist_ok=True)
    (CLIPS / "raw").mkdir(exist_ok=True)
    base, server, thread = start_server(state)
    print(f"console on {base} (isolated state)")

    from playwright.sync_api import sync_playwright

    made = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, fn in SCENES:
            if args.only and args.only not in name:
                continue
            t0 = time.perf_counter()
            scene = Scene(browser, base, name)
            fn(scene)
            raw = scene.close()
            mp4 = CLIPS / f"{name}.mp4"
            to_mp4(raw, mp4)
            made.append(mp4)
            print(f"  {name:12} {time.perf_counter() - t0:5.1f}s  ->  {mp4.name}")
        browser.close()

    server.should_exit = True
    thread.join(timeout=10)

    if args.assemble:
        build = OUT / "build"
        build.mkdir(parents=True, exist_ok=True)
        parts = []
        for item, secs in CUT:
            kind, name = item.split(":")
            dst = build / f"{len(parts):02d}-{name}.mp4"
            if kind == "card":
                to_mp4(ASSETS / f"{name}.png", dst, secs)
            else:
                src = CLIPS / f"{name}.mp4"
                if not src.exists():
                    print(f"  missing clip {name}, skipping")
                    continue
                shutil.copy2(src, dst)
            parts.append(dst)
        listing = build / "concat.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        final = OUT / "fraudgraph-demo-reference.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c", "copy", str(final)], check=True)
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(final)],
                             capture_output=True, text=True).stdout.strip()
        print(f"\n{final}  --  {float(dur):.0f}s, {final.stat().st_size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
