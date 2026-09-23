# Recording, editing and quality control

## 1. Setup, in order

```bash
python -m fraudgraph.benchmark.validate    # must print PASSED
python run_api.py                          # http://127.0.0.1:8077
```

- [ ] **Savanna workspace started ten minutes ahead.** The status strip must
      read `Graph tigergraph`. If it reads `local (fallback)` the workspace is
      asleep — true, but weaker on camera.
- [ ] `GEMINI_API_KEY` set in `.env`, so the strip reads `LLM on` and the live
      feed shows the narration step. **Check your quota first:** the free tier
      is 20 requests a day and each live run spends two.
- [ ] Console open at **1920×1080**, browser zoom **100%**, full screen.
- [ ] Light theme.
- [ ] Notifications, chat apps and calendar alerts off.
- [ ] A rehearsal run of scene 5 done, so the LLM wait is warm and you know
      roughly how long it takes today.
- [ ] Optional: restore the published answers afterwards with
      `python -m fraudgraph.benchmark.run` (your recording will leave HHG-014
      as a live re-run and an approval recorded).

## 2. Assets you need

Already generated — nothing to make:

- [ ] `assets/01-title.png`, `06-end.png` — opening and closing cards
- [ ] `assets/02-…`, `03-…`, `04-…`, `05-section-measured.png` — four section cards
- [ ] `assets/lt-simulated.png`, `lt-hhg014.png`, `lt-hhg007.png`, `lt-backtest.png`
      — transparent lower thirds
- [ ] `reference/fraudgraph-demo-reference.mp4` — pacing guide, or the base cut
- [ ] `reference/clips/*.mp4` — per-scene backup footage

To replace: only your own screen capture, if you record instead of narrating
the reference.

## 3. Recording

- [ ] Record video and voice on **separate tracks** — retiming narration is
      then trivial, and a fluffed line costs one take, not one scene.
- [ ] Follow `STORYBOARD.md` row by row. Pause 1s between scenes; it gives the
      editor clean cut points.
- [ ] Speak *after* the click lands, not during — the viewer needs to see what
      changed before hearing why it matters.
- [ ] Let the graph settle before talking over it (about 2 seconds).
- [ ] Do the whole thing twice. The second take is almost always the keeper.

## 4. Editing

- [ ] Assemble in storyboard order: card, scene, card, scene…
- [ ] Hard cuts between cards and footage; a 0.3s cross-dissolve only at the
      very start and very end.
- [ ] Drop the four lower thirds at the times in the storyboard, 6–8s each,
      fading in and out over 0.25s.
- [ ] Cut the LLM wait in scene 5 down to 2–3 seconds.
- [ ] Trim to **under 5:00**. Target 4:00–4:30.
- [ ] Export H.264 MP4, 1920×1080, 30fps, ~10 Mbps, AAC audio 192 kbps.
- [ ] Normalise voice to about −16 LUFS; no background music (it competes with
      narration and adds nothing here).

## 5. Quality control before upload

**Technical accuracy**

- [ ] No accuracy figure for the 20 exam cases is stated anywhere.
- [ ] No action is described in the past tense as having happened.
- [ ] The backend named in the narration matches the status strip on screen.
- [ ] Every number spoken matches what is visible: 590,742 · 18 of 20 · 28
      cards · 1 profile in 9,706 · 0.05 → 0.98 · 9 of 9 · 7 in 300 · 310 tests.
- [ ] The words "simulated" and "awaiting approval" are audible at least once.

**Narration sync**

- [ ] Each sentence lands while its subject is on screen.
- [ ] No sentence starts before the click that causes it.
- [ ] No dead air longer than ~2s.

**Visual**

- [ ] 1080p throughout; no scaled-up or blurry segments.
- [ ] No browser chrome, bookmarks, notifications, or other windows in shot.
- [ ] Text legible at 50% player size — if not, zoom the browser to 110% and
      re-record that scene.
- [ ] Cards and footage share the same paper background (they do by design).

**Completeness**

- [ ] Problem, solution, live demonstration, policy/approval, measured
      results, and close are all present.
- [ ] TigerGraph is shown, not just mentioned.
- [ ] The repo URL is readable on the end card.

**Watermarks and branding**

- [ ] No recorder watermark (OBS adds none; free trials of commercial
      recorders often do — check the corners of the exported file).
- [ ] No stock footage, no music, no third-party logos.
- [ ] Nothing on screen belongs to anyone but this project and TigerGraph's
      own product UI.

## 6. Upload

- [ ] YouTube, **Unlisted** — viewable by link, no approval delay.
- [ ] Title: `fraudgraph — agentic fraud investigation on TigerGraph (HHGoa 2026)`
- [ ] Description: repo link, console link, and one line stating that all
      actions are simulated.
- [ ] Open the link in a private window and watch the first 15 seconds before
      pasting it into the submission form.
