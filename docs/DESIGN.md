# Console design system

The console is set like an investigation file, not a metrics dashboard. The
product's claim is that a score is a reason to look and never a verdict, and
that every conclusion carries the evidence behind it. So: ink on paper, ruled
sections, identifiers in type you would copy, and colour reserved for verdicts —
the only saturated marks on the page, used like stamps.

Everything below lives as tokens at the top of `frontend/styles.css`.

## Colour

Light ("paper") is the designed default: dense tabular text reads better dark on
light, and it projects well. Dark ("graphite", deliberately not navy) follows the
system setting, and the header toggle overrides either way (stored per browser).

| role | light | dark | use |
|---|---|---|---|
| `--paper` | `#f6f4ee` | `#141412` | page |
| `--sheet` | `#fffdf8` | `#1c1b19` | contained surfaces |
| `--wash` | `#ece8df` | `#282622` | hover, placeholders, code |
| `--ink` / `-2` / `-3` | `#1b1a17` / `#4d4a43` / `#66625a` | `#ece9e1` / `#bdb9ae` / `#9d988c` | text, three levels |
| `--accent` | `#274690` | `#9bb1e6` | things you can act on — nothing else |
| `--fraud` / `--legit` / `--uncertain` | `#a3261b` / `#2c6639` / `#855606` | `#f08d7f` / `#86c596` / `#dfae5c` | verdicts only |

**Every text colour measures ≥ 4.5:1 against every surface in both themes**,
the verdict and accent washes included (worst case: `--ink-3` on `--fraud-wash`,
4.90:1 light; `--ink-3` on `--uncertain-wash`, 4.96:1 dark). As rendered, the
lowest-contrast text anywhere in the console is 4.96:1 in either theme. The
palette it replaced failed AA in five places, including the primary button
(3.20:1).

No colour literal appears outside the token blocks, except white paper for
print. Dark is written twice (system setting, toggle) and the two are identical.

## Type

Three roles, each with a reason:

* **Serif** (`Charter`, `Sitka Text`, `Iowan Old Style`, `Cambria`, `Georgia`) —
  view titles and the agent's written conclusions: the case summary and the SAR
  narrative. Prose a person would sign.
* **Sans** (system UI) — the interface.
* **Mono** — identifiers and queries: case, card and transaction ids, `query:…`
  refs. Things you would copy.

All three are system fonts: nothing is downloaded, so the console still works
offline. Scale: **12 · 13 · 14 · 16 · 20 · 26 · 34 px**, nothing smaller than 12.
Figures are tabular everywhere.

## Space, shape, motion

* Spacing: **4 · 8 · 12 · 16 · 24 · 32 · 48 px**.
* Radius: **2px** on controls and marks, **4px** on containers. Two values
  (the old sheet had thirteen).
* Motion reports state and nothing else: the live feed revealing lines, the
  running indicator. 120ms and 180ms, one easing. `prefers-reduced-motion` is
  honoured. Loading placeholders hold still rather than shimmer.

## Components

| component | what it is | rule |
|---|---|---|
| `.sec` | a heading on a rule, then content | sections are not boxes |
| `.sheet`, `.aside` | a contained surface | only when containment means something: a chart, a log, a table, the decision column |
| `.ledger` | label/value readouts | the one way to show figures; replaces KPI tiles, fact tiles, stat tiles. `.fit` for 3–4 figures |
| `.stamp` | a verdict | the only uppercase element on the page |
| `.tag` | neutral metadata | colour only when it means something (an L2 route, a pending approval) |
| `.alert` | something to notice | never for plain information |
| `.callout`, `.note` | a margin note | a rule in the verdict colour, not a card |
| `.prose` | the agent's conclusion | serif, 68ch measure |

## Tables

* Nothing wraps by default: an identifier broken across two lines is misread.
  Descriptive labels (trigger, pattern) opt in with `.wrap` and may wrap when
  space runs out.
* Columns marked `.opt` drop out between 961 and 1320px instead of making every
  row scroll sideways.
* Tables with few columns take `.table-wrap.fit`, keeping their natural width.
* Below 960px the queue becomes one card per case, each cell printing its label.

## Responsive

| width | change |
|---|---|
| ≤ 1320 | brand description hidden; optional queue columns hidden |
| ≤ 1180 | chart and margin notes stack; case view to one column |
| ≤ 960 | header becomes three static rows (brand, sections, status); queue to cards |
| ≤ 640 | ledgers two-up; tighter tabs |
| touch | controls at 40px |

"Actions simulated" is the first status item and stays visible at every width.

## How it was checked

* Contrast: every token pair computed with the WCAG formula, both themes; and
  every visible run of text as rendered — HTML, SVG labels, form values,
  placeholders — against the background actually behind it.
* Layout: 320, 375, 414, 768, 1024, 1280, 1440 and 1920px across all five
  views — no page-level horizontal overflow and no element past the viewport
  edge at any of them; the queue table fits without scrolling from 1024 up.
* Behaviour: 29 interaction checks in the browser — navigation, filters, sort,
  every route into a case, approve and reject, both re-run paths including the
  live stream, ad-hoc investigation, graph, theme — with no console errors.

All of it is automated.

* `tests/test_design_tokens.py` (no browser, under a second) checks the token
  matrix in both themes, that the two dark blocks agree, that dark overrides
  every colour light sets, and that no colour bypasses a token in the CSS,
  `app.js` or `index.html`.
* `tests/test_frontend.py` (`python -m pytest -m browser`) holds the 29 checks
  as one test each, the layout sweep at all eight widths, a check that
  "Actions simulated" is never cut off, and the rendered contrast check: about
  1,300 runs of text per pass, in light and dark at 1440 and 375px, across
  every view, both tooltips, a hovered row and button, the toast, the live
  investigation, an error box and a drifted re-run. Large text is held to
  3:1, everything else to 4.5:1; disabled controls are exempt, as in WCAG.
  Any uncaught exception or console error fails the test it happened in.

The tests were checked against deliberate breakage, and each was caught: no
approver name, a one-way sort, wall-clock latency in the live panel, an
unsaved theme, an element wider than the page, a paler `--ink-3`, a colour
literal, a stamp set in its own ink, text faded with opacity, chart labels in
a rule colour, and the two dark blocks drifting apart. The middle three pass
the token tests — two valid tokens paired badly, opacity, an SVG fill — and
only the rendered check sees them.

The rendered check composites the backgrounds of a text's ancestors. It would
miss text laid over a positioned sibling; the console has none (bars sit
beside their figures, tooltips carry their own background). Hover states
beyond the two it forces use only `--wash` and `--accent-strong`, which the
token matrix covers.
