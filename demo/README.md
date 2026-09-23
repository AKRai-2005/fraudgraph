# Demo video package

Everything needed to record the submission video, plus a reference cut already
built from the real console.

**Audience.** Hackathon judges scoring the TigerGraph × Hacker House Goa task
in 3–5 minutes, who will have watched several similar submissions. It works
unchanged for a project evaluation or a technical showcase.

**Running time.** The reference cut is **3:51**. The narration written against
it is ~530 words, which lands at 3:45–4:00 spoken normally — inside the
five-minute limit with room for pauses.

| File | What it is |
|---|---|
| [`SCRIPT.md`](SCRIPT.md) | The narration, line by line, with timestamps |
| [`STORYBOARD.md`](STORYBOARD.md) | Scene table: visuals, actions, narration, editing notes |
| [`CHECKLIST.md`](CHECKLIST.md) | Setup, recording, editing and quality-control checklists |
| `assets/*.png` | Title, section and end cards (1920×1080) and four lower-third overlays (transparent) |
| `reference/fraudgraph-demo-reference.mp4` | 1080p silent reference cut, 3:51 |
| `reference/clips/*.mp4` | The same footage as six separate scene clips |

## Two ways to use this

**A. Narrate the reference cut** (fastest, ~20 minutes). Open the reference
video in any editor, record voice-over against the timestamps in `SCRIPT.md`,
export. Nothing else to capture.

**B. Record your own screen** (best, ~1 hour). Follow `STORYBOARD.md` — it says
what to click and what to say, in order. Use the reference cut as the pacing
guide, and drop in the cards from `assets/` at the marked points.

B is worth the extra time for one reason: your own recording can run against
**TigerGraph** with the **LLM on**, and the reference cut cannot — see below.

## What the reference cut is, honestly

It is the real application, driven by `scripts/demo_record.py`, recorded at
1080p. Nothing is mocked, no interface was faked, and every figure on screen
comes from the published answer files. Three things differ from what you
should record yourself:

* **The graph backend is the local mirror**, so the status strip reads
  `local (fallback)`. With your Savanna workspace running it reads
  `tigergraph`, which is what a judge should see.
* **The LLM is switched off**, so the case header says `no LLM narration` and
  the live feed has no narration step. It was switched off to keep the pacing
  tight and to spend no free-tier quota. With `GEMINI_API_KEY` set, the feed
  gains a line saying the model is writing, and the wait is 7–58 seconds.
* **The pointer is drawn by the recorder.** Headless Chromium records no
  cursor, so a small circle is drawn at each click. Your own recording will
  show a real cursor.

It also writes to an isolated copy of the data (`build/demo_state`), so the
approval it performs never touches the demo state the console serves.

## Rebuilding any of it

```bash
python scripts/demo_assets.py                 # the cards and overlays
python scripts/demo_record.py --assemble      # the clips and the reference cut
```

No watermarks are applied anywhere, and every asset is generated from this
project's own design tokens.
