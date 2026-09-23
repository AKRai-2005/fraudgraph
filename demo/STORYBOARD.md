# Storyboard and recording plan

Twelve segments, 3:51 total. Timestamps match
`reference/fraudgraph-demo-reference.mp4`, so you can either narrate over that
file or reproduce each row on your own screen.

**Before you start:** complete the setup in [`CHECKLIST.md`](CHECKLIST.md) —
workspace running, console open at 1920×1080, browser zoom 100%, notifications
off.

| # | Start | Length | On screen | What you do | Narration (abridged — full text in SCRIPT.md) | Editing |
|---|---|---|---|---|---|---|
| 1 | 0:00 | 12s | `assets/01-title.png` | Nothing — still card | "A bank's fraud model gives every alert a score… investigates it in a graph of 590,742 transactions and decides for itself." | Hold still. Fade up from paper-white, 0.3s |
| 2 | 0:12 | 4s | `assets/02-section-disagreement.png` | Nothing | Silence, or "Start with the argument for the whole thing." | Hard cut in, hard cut out |
| 3 | 0:16 | 33s | Console, **Overview** tab | Let the chart settle. Point at the sentence above it. Hover the top-left point (HHG-014), then the bottom-right (HHG-007). Point at the status strip. Scroll slightly to the margin notes | "Along the bottom is the bank model's risk score… eighteen of the twenty… actions are simulated." | Overlay `lt-simulated.png` at 0:41–0:47 |
| 4 | 0:49 | 4s | `assets/03-section-live.png` | Nothing | Silence | Hard cut |
| 5 | 0:53 | 37s | Console, **Case** view, live panel | Click the HHG-014 point, then **Watch it investigate**. Let the feed stream. Scroll the feed slowly to the bottom. Point at the final verdict line | "This is the agent running now, not a replay… twenty-eight cards… it stops, citing the policy rule." | Overlay `lt-hhg014.png` at 1:05–1:12. **If the LLM wait runs long, cut it here** — see note below |
| 6 | 1:30 | 37s | Case: header, evidence, graph | Scroll to the figures row, then into **Evidence**. Stop on the shared-device claim. Scroll to **The graph around the alert**, hover the orange device node | "Every claim carries the query that produced it… one profile out of 9,706… that's the ring, two hops out." | Let the graph settle 2s before speaking over it |
| 7 | 2:07 | 4s | `assets/04-section-policy.png` | Nothing | Silence | Hard cut |
| 8 | 2:11 | 28s | Case: **Decision** column | Scroll to Decision. Point at the `L1` and `L2` tags. Type your name in the field, click **Approve** on `BLOCK_CARD`. Let the simulated-execution line appear | "Every action cites the rule that produced it… an approval needs a name… the system never claims a card was blocked." | Zoom 110% on the approval row if your editor makes it easy |
| 9 | 2:39 | 23s | Case **HHG-007**, Decision | Open HHG-007 (bottom-right chart point). Scroll to **Before the evidence request** and **What changed** | "This is the alert the bank scored 0.87… the cardholder confirmed, and both of those actions came off." | Overlay `lt-hhg007.png` throughout |
| 10 | 3:02 | 4s | `assets/05-section-measured.png` | Nothing | Silence | Hard cut |
| 11 | 3:06 | 35s | **Model & policy** tab | Open the tab. Scroll through the backtest table, then the logistic-fit table | "We claim no accuracy on the twenty… all nine undocumented-typology frauds… it scored 0.96 and was useless." | Overlay `lt-backtest.png` at 3:18–3:26 |
| 12 | 3:41 | 10s | `assets/06-end.png` | Nothing | "Twenty answer files, validated… three hundred and ten tests. It's all reproducible — thank you." | Fade to paper-white over the last 0.5s |

## The one risky moment, and the backup

Scene 5 is the only live thing in the video. With `GEMINI_API_KEY` set, the
narration step takes **7 to 58 seconds**, and you cannot predict which.

* **Preferred:** let it run, and while it waits say the "the language model
  only writes the summary" line — the feed itself says the model is writing.
  Cut the dead air in the edit.
* **If it hangs past ~60s:** stop recording, use `reference/clips/03-live.mp4`
  for that segment, and say nothing about narration timing.
* **If the stream fails outright:** click **Re-run without streaming**
  instead. The case updates and the provenance line says it is a live re-run.
  Narrate the same evidence points; skip the "every line appears as it
  happens" sentence.

## Capture settings

* 1920×1080, 30fps, browser at 100% zoom, full screen (F11) so no tabs or
  bookmarks are in shot.
* Record system audio off; voice on a separate track so you can retime it.
* Light theme (the console follows your OS; the toggle is in the header).
* Close anything that can raise a notification.

## Presenter placement

No webcam is needed — the console is the subject, and a face box would cover
the right-hand Decision column. If your evaluation requires you on screen, put
a small circle in the **bottom-left**, which stays empty in every scene above.
