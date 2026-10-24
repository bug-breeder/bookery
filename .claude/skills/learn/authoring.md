# lesson.json reference

The vocabulary the runtime renders and the gates check. It mirrors
`learn/schema.py`; if they disagree, the schema wins (and fix this file).

## The lesson

```json
{
  "schema": 1,
  "language": "en",
  "title": "Fairness Has a Price",
  "subtitle": "What dictator, ultimatum and trust games reveal about social preferences",
  "minutes": 25,
  "objectives": [
    "Predict what proposers offer and responders reject in ultimatum games",
    "Explain why dictator games separate fairness from strategy"
  ],
  "sections": [
    { "title": "The ultimatum game", "slides": [ ... ] }
  ],
  "skipped": [
    { "section": "2.6", "reason": "Methodological detail; its conclusion is on slide 'stakes'." }
  ],
  "glossary": [
    { "term": "Responder", "def": "The player who accepts or rejects the offer.",
      "example": "Offered $3 of $10, the responder says yes (she gets $3) or no (both get $0).",
      "sources": ["b0031"] },
    { "term": "Dictator game", "aliases": ["dictator", "dictators"],
      "def": "An ultimatum game without the veto: the proposer splits the money and the responder must take whatever she's given.",
      "example": "The dictator keeps $8 of $10 and gives $2. That's final; the other player can't refuse.",
      "sources": ["b0052"] }
  ],
  "takeaways": ["Offers cluster at 40-50 percent; offers under 20 percent are rejected about half the time."]
}
```

- `objectives`: 2-5, phrased as things the reader can do.
- `minutes`: honest study time (about 1 minute per slide, more for explorables).
- `sections`: the lesson's own parts. They need not mirror the chapter's
  sections; coverage is computed from the blocks your slides cite.
- `skipped`: chapter sections (by label as the pack lists it: `2.6`, or
  `s3` for unnumbered books) you deliberately leave out, each with a reason
  the reader sees.
- `glossary`: **every term the lesson teaches** (see "The glossary and
  `teaches`" below). Each needs `def` (≤ 40 words, plain, no undefined
  words), `example` (≤ 30 words: the term at work in one concrete case) and
  `sources`; `aliases` lists other ways slides name it ("dictators").
- `takeaways`: 3-6 lines for the recap. Numbers here are checked too.

## A slide

```json
{
  "id": "offers-cluster",
  "kicker": "Ultimatum games",
  "title": "Most proposers offer close to half",
  "text": "Across dozens of studies the modal offer is 40-50 percent.",
  "points": ["...", "..."],
  "sources": ["b0034", "b0035"],
  "teaches": ["Ultimatum game"],   // glossary terms this slide introduces

  "figure": { ... }          // or exactly one of: widget, stats, quote, columns

  "deeper": "More detail for the curious, under the slide in study mode.",
  "notes": "What a presenter says over this slide.",
  "beyond": "Context from outside the book, labelled as such."
}
```

| field | what | budget |
| --- | --- | --- |
| `id` | kebab-case, unique, ≤ 48 chars; used in links (`#offers-cluster`) | |
| `layout` | omit (inferred). `"title"` and `"section"` are dividers (present mode only); `"statement"` is one big sentence | |
| `kicker` | tiny label above the title | 6 words |
| `title` | the slide's claim, not its topic ("Offers cluster near half", not "Offers") | 12 words |
| `text` | one to three sentences | 50 words |
| `points` | bullets | 5 × 18 words |
| `sources` | block ids (`b0034`) this slide condenses. Required on every content slide | |
| `teaches` | glossary terms first taught here; each shows as a definition card (after `text`, before `points`) | |
| `deeper` | study mode only, in smaller type under the slide; extra detail, never something the slide needs | 160 words |
| `notes` | present mode notes drawer | 200 words |
| `beyond` | outside knowledge, rendered under "Beyond the book"; exempt from the numbers/quotes gates | 80 words |

Visible words per slide (title + kicker + text + points + stats + quote +
columns) are capped at 90; definition cards have their own budget. Present mode shows one slide on a 1600×900
stage in large type; the runtime gate fails a slide that overflows it.

**One main part per slide**: `figure`, `widget`, `stats`, `quote` or
`columns`, never two. Text and points can accompany any of them; keep them
short when they do (a figure plus 5 long points will overflow).

### The glossary and `teaches`

The reader never opens the book, so a term's first appearance must be the
slide that teaches it. List the term in that slide's `teaches`: the page
shows a "New term" card with its `def` and `example`, and later slides can
underline it with `[[term]]` for a hover reminder. The `teaching` gate
fails when:

- any reader-facing string (title, kicker, text, points, a question, an
  option, a `why`, a caption, a figure note, `deeper`, `beyond`, a lesson
  section's title, or a definition card shown on that slide) names a term
  before its teaching slide. Matching is case-insensitive on the term,
  its `aliases`, and their plurals;
- a glossary term is taught by no slide, or by two;
- a `[[term]]` names something not in the glossary.

The lesson's title, subtitle and objectives are exempt: they preview.

What to declare: every word a smart outsider wouldn't know or that the
book uses in a special sense, including games and setups ("dictator
game"), roles ("trustee"), measures ("MAO"), named theories
("Fehr–Schmidt"), notation ("\\(\\alpha\\)", give it aliases like
`"\\alpha"`), and coined phrases ("Goldilocks zone"). A definition uses
only words already taught or plain English. Teach several related terms on
one slide when they only make sense together (a game and its two roles).

### Inline markup

In any reader-facing string: `**bold**`, `*italic*`, `` `code` ``,
`[[term]]` or `[[term|shown text]]` (glossary hover), and math:
`\( x^2 \)` inline, `\[ ... \]` or `$$ ... $$` display. A single `$...$`
also works, but money like "$10" is safe only because a digit follows the
`$`; prefer `\( \)` for inline math. Numbers inside math are checked like
any other number. In JSON, backslashes are doubled: `"\\( \\alpha \\)"`.

## Main parts

### figure: one of the book's exhibits

```json
"figure": {
  "ref": "fig 2.3",
  "caption": "Optional: replaces the book's caption",
  "alt": "Optional: describes the image for screen readers",
  "notes": [
    { "x": 0.18, "y": 0.72, "text": "Offers of 50 percent are almost never rejected" },
    { "x": 0.62, "y": 0.35, "text": "Rejections climb below 20 percent" }
  ]
}
```

`ref` is `fig N.N`, `tbl N.N`, or the block id of an unlabelled exhibit
(from the pack's exhibit table). It must have an image; a recovered matrix
without one should become a `table` widget. `notes` pin numbered markers
at `x`/`y`, fractions of the image's width/height from its top-left: look
at the image before placing them, and put each marker beside what it points
at, never on top of it (a marker hides a table value or an icon). In present mode each note appears on
"next", so a presenter can walk through the figure. The figure's block
counts as a source automatically.

### stats: 1-3 big numbers

```json
"stats": [
  { "value": "40-50%", "label": "typical offer", "context": "modal offer across studies" },
  { "value": "~50%", "label": "of offers under 20% rejected" }
]
```

`value` and `label` are strings; every number in them is checked.

### quote: verbatim from the book

```json
"quote": { "text": "Responders reject ... offers below 20 percent about half the time.", "cite": "The author", "block": "b0035" }
```

`cite` names who said it (optional); the page is added from `block`, so
don't write page numbers yourself. Must be verbatim (case, punctuation and line breaks are normalised;
hyphenation too). `...` joins verbatim pieces. Put the quote's block in
`block` or `sources`. ≤ 45 words.

### columns: 2-3 side-by-side comparisons

```json
"columns": [
  { "title": "Dictator game", "text": "Responder can't refuse." },
  { "title": "Ultimatum game", "points": ["Responder can reject", "Both then get nothing"] }
]
```

≤ 30 words per column.

## Widgets

`"widget": { "type": "...", ... }`. Interactive types (quiz, predict,
steps, flashcards, match, sort, explore, and custom unless `static`) get a
"Your turn" tag; quiz, predict, match and sort are recall checks (a lesson
needs at least one). There is no quota: see "Interactions that earn their
place" below. Every widget may have a `caption` (an instruction line).

### quiz

```json
{
  "type": "quiz",
  "question": "A responder rejects a $2 offer out of $10. What does that cost them?",
  "options": [
    { "text": "$2", "correct": true, "why": "Rejecting means both get nothing, so the responder gives up the $2." },
    { "text": "Nothing", "why": "They do lose: a rejection leaves both players with zero." },
    { "text": "$8", "why": "$8 is what the *proposer* loses." }
  ],
  "explain": "Optional: shown after answering."
}
```

2-6 options, exactly one `"correct": true` (or `"multi": true` with one or
more, plus a Check button). Every option needs a `why`. Wrong options'
text is exempt from the numbers gate (they're meant to be wrong); `why`
is not. Question ≤ 35 words, options ≤ 20, why ≤ 40.

### predict: commit to a guess, then see the book's answer

```json
{
  "type": "predict",
  "question": "What share of offers below 20 percent do responders reject?",
  "input": { "kind": "slider", "min": 0, "max": 100, "step": 5, "value": 20, "unit": "%", "label": "Rejected" },
  "answer": 50,
  "tolerance": 10,
  "reveal": "About half. People pay to punish offers they see as unfair."
}
```

or a choice: `"input": { "kind": "choice", "options": ["Under 10%", "About half", "Over 90%"] }, "answer": "About half"`.
`answer` is checked against the chapter; `min`/`max`/`step`/`value`/
`tolerance` are controls and aren't. `decimals` sets display precision.

### chart: the book's numbers, drawn

```json
{
  "type": "chart",
  "chart": "bar",
  "x": { "label": "Offer (share of pie)", "values": ["0-10%", "10-20%", "20-30%", "30-40%", "40-50%"] },
  "y": { "label": "Rejection rate", "unit": "%", "min": 0, "max": 100 },
  "series": [
    { "name": "Rejected", "values": [60, 48, 22, 9, 2] }
  ],
  "highlight": 1,
  "labels": true,
  "note": "Optional line under the chart.",
  "derived": "Optional: how values were computed from the book (e.g. averaged over the three studies in table 2.2)"
}
```

- `bar`: categories in `x.values`, one value per category per series
  (`null` for a gap). Up to 4 series (grouped bars, legend shown).
- `line`: numeric `x.values`; series `values` as for bar.
- `scatter`: series with `points: [[x, y], [x, y, "label"], ...]`.
- `highlight` (bar): index of the category to emphasise; others dim.
  `labels: true` prints values on bars; keep that for ≤ 8 bars.
- Every value must be a number from the chapter (cite the table's block).
  If you computed values (averages, differences), say how in `derived`:
  they're then listed for review instead of failing. If you read them off
  a book image, see `transcribed` under custom.
- The reader gets hover tooltips and a "Show data" table automatically.

### table

```json
{ "type": "table", "columns": ["Study", "Mean offer", "Rejections"], "rows": [["Pittsburgh", 0.45, "22%"], ["Tokyo", 0.44, "11%"]], "highlight": 0, "note": "Optional caption" }
```

1-14 rows. Numeric cells are checked. `decimals` sets number formatting.

### steps: click through a sequence

```json
{
  "type": "steps",
  "steps": [
    { "title": "Proposer offers", "text": "Splits $10: keeps $6, offers $4." },
    { "title": "Responder decides", "text": "Accept: $6/$4. Reject: $0/$0.", "figure": { "ref": "fig 2.1" } }
  ]
}
```

2-8 steps, each with a `title`, optional `text` and `figure` (`ref`, `caption`).
In present mode, "next" advances the steps before the slide.

### flashcards

```json
{ "type": "flashcards", "cards": [ { "front": "Dictator game", "back": "Proposer splits; responder must accept." } ] }
```

2-12 cards. In present mode "next" flips, then advances.

### match: pair items

```json
{ "type": "match", "prompt": "Match each game to what it measures",
  "pairs": [ { "left": "Dictator", "right": "Pure altruism" }, { "left": "Ultimatum", "right": "Fairness + fear of rejection" }, { "left": "Trust", "right": "Trust and reciprocity" } ],
  "explain": "Optional" }
```

3-6 pairs; the right column is shuffled (deterministically).

### sort: put items in buckets

```json
{ "type": "sort", "prompt": "Which explanation fits?",
  "buckets": ["Social preference", "Strategic"],
  "items": [ { "text": "Offering half to avoid rejection", "bucket": "Strategic", "why": "Fear of rejection, not fairness." } ] }
```

2-4 buckets, 3-10 items, each with a `bucket` (one of `buckets`) and an
optional `why` shown when it's misplaced.

### explore: sliders driving a formula from the chapter

```json
{
  "type": "explore",
  "params": [
    { "id": "x", "label": "Offer \\(x\\)", "min": 0, "max": 10, "step": 0.5, "value": 3, "unit": "" },
    { "id": "a", "label": "Envy weight \\(\\alpha\\)", "min": 0, "max": 2, "step": 0.1, "value": 0.5, "decimals": 1 }
  ],
  "outputs": [
    { "label": "Responder utility", "expr": "x - a * max(10 - 2*x, 0)", "decimals": 2 }
  ],
  "verdicts": [
    { "when": "x - a * max(10 - 2*x, 0) < 0", "text": "**Reject**: rejecting (utility 0) beats this offer." },
    { "when": "true", "text": "**Accept**." }
  ],
  "plot": { "over": "x", "y": [ { "name": "Utility", "expr": "x - a * max(10 - 2*x, 0)" } ], "xLabel": "Offer", "yLabel": "Utility" },
  "note": "Optional line under it."
}
```

- `expr`, `when`: JavaScript expressions over the param ids and `min max
  abs pow sqrt exp log round floor ceil sign clamp PI E`. The first
  matching verdict is shown.
- The formula must be the chapter's (cite its equation block). The gates
  can't check computed outputs, so keep expressions faithful to the book.
- `plot.over` names the param on the x axis; the current value is marked.
  Up to 4 curves; `yMin`/`yMax` fix the y range.

### custom: a figure or tool written for this chapter

The default for figures, and for any interaction the built-ins can't
express (a game against the book's subjects, a simulation, a tool). Draw
what teaches the idea best: a pipeline with gates, a spreadsheet whose
columns fill in and whose rows re-rank, a map, a timeline. Don't reach
for a built-in type because it's there. Write
`lessons/chNN/widgets/<module>.js`:

```js
Bookery.widget("offer-game", function (el, props, api) {
  // el    the container to render into
  // props this widget's "props" from lesson.json
  // api   { h, svg, md, mdEl, chart, fmt, shuffle, rng, mode, slideId,
  //         record(ok), onNext(fn), figure(key) }
  var out = api.h("p", { class: "bk-text" });
  var btn = api.h("button", { class: "bk-btn bk-btn-turn", type: "button", text: "Offer $" + props.offer });
  btn.addEventListener("click", function () { out.textContent = props.acceptRate + "% of responders accept."; api.record(true); });
  el.append(btn, out);
});
```

and on the slide: `"widget": { "type": "custom", "module": "offer-game", "caption": "Make an offer and see how responders in the study reacted.", "props": { "offer": 2, "acceptRate": 50 } }`.

- `api.h(tag, attrs, ...children)` builds elements (`class`, `text`,
  `html`, `on<event>`); `api.chart(el, spec)` draws a chart widget spec;
  `api.md(s)` renders inline markup to HTML; `api.rng(seed)` is a seeded
  random generator; `api.record(ok)` counts a recall result;
  `api.onNext(fn)` adds a present-mode build (return true while it
  consumed the "next").
- Plain browser JavaScript, no imports, no network. Use the runtime's
  classes (`bk-btn`, `bk-btn-turn`, `bk-option`, `bk-slider`, `bk-feedback
  is-good|is-bad`, `bk-row`, `bk-table`) and colour tokens
  (`var(--turn)`, `var(--book)`, `var(--ink-2)`, `var(--series-1)`…) so
  it matches both themes.
- The page has a clean app look in the manner of HeroUI; keep a widget
  in that language. White cards on a white page, zinc fills for quiet
  surfaces (`--sunk`, hover `--sunk-2`), Inter throughout (`--font-ui`,
  numbers with `font-variant-numeric: tabular-nums`), labels in sentence
  case at `0.8em`, weight 600, `--ink-3`. One accent, lime: everything
  the reader does (`--turn` as a fill, text on it `--on-turn`, tints
  `--turn-wash` / `--turn-tint`). Lime is too light for text or thin
  lines on white: use `--turn-ink` for those. A highlighted row or cell
  is `--book-wash`; a selected thing is a dark ring (`--ink`), not
  colour alone; right and wrong are `--good` / `--bad` with a ✓ or ✕.
  Corners: `var(--radius-sm)` (8px) on small parts, `var(--radius)` on
  inputs and tiles, `var(--radius-lg)` on cards; buttons and chips are
  pills (`999px`). Inputs are flat zinc fields with a `--turn-ink` focus
  ring; tabs are a segmented control (a zinc track, the current tab a
  raised white pill with `var(--shadow)`). No gradients, no coloured
  card tops, no uppercase or monospace labels.
- Numeric `props` are checked against the chapter, except control keys
  (`min max step value seed rounds width height delay speed default
  decimals tolerance`). Strings in `props` are checked like slide text.
- `"static": true` marks a figure: it counts as visual, gets no "Your
  turn" tag, and isn't counted as interactive. Its caption reads as a
  figure caption. Controls that only step through a figure (column by
  column) are still static.
- `"wide": true` keeps the widget at the stage's full width in present
  mode even when the slide `teaches` a term: the text and the term cards
  share one row above it instead of a column beside it. Use it for wide
  figures (a pipeline of stages, a many-column sheet).
- `"transcribed": ["ch03-b0052", ...]` names the book images the widget's
  numbers were read from (a spreadsheet screenshot, a chart with no
  numbers in the text). Those numbers are listed under
  `transcribed_for_review` instead of failing `numbers`; the images count
  as exhibits used and the page shows them beside the widget ("Numbers
  read from the book's figures · check them"). `api.figure(key)` returns
  them too. Also allowed on `chart`, `table` and `explore`.
- Styling: inject one `<style>` (guarded by an id) with class names
  prefixed per module; size in `em` so the same code fits study (16px)
  and the present stage (24px); a custom property in `em` resolves
  against the font size of the element using it, so keep row-height
  slots on elements with the rows' font size. Use a container query
  (`container-type: inline-size` on a wrapper) for phone width, honour
  `prefers-reduced-motion`, and check `api.mode === "present"` for a
  layout that fits the 1600×900 stage (side panels beat stacked rows).
- `api.onNext(fn)` turns a figure's steps into present-mode builds;
  `bookery learn shot --present --slide ID` shows it after all builds.
  The transcript steps through them too, reading the page's text
  (`innerText`) after each: hide what a step hasn't revealed yet with
  `visibility: hidden` or `display: none`, not just `opacity: 0`, and if
  the order on screen comes from transforms, say it in words too (a
  visually hidden line that a screen reader also gets).
- Persist a tool's input with `localStorage` wrapped in try/catch (it can
  be blocked); offer a copy button (`navigator.clipboard.writeText` inside
  the click, with a select-the-text fallback) rather than a download.
- A thrown error is caught and shown in place, and fails the runtime gate.

## Pedagogy: teach, then ask

- **Open with the question that makes the idea matter**, then teach the
  idea. A surprising number only works once the reader knows what it
  measures and what they'd have expected.
- **Teach a setup completely before using it.** For a game: who the
  players are, what each can do, what each choice pays, in one worked
  case. A picture of the moves beats a paragraph.
- **State the baseline.** Before data that breaks a prediction, say what
  the prediction is and why: "A purely selfish responder accepts any
  offer above zero, so a selfish proposer offers the smallest amount."
- **Predict, then reveal**, but only on a setup the reader fully knows.
  Guessing first makes the answer stick when the reader has something to
  reason from (an intuition about a game they understand, a calculation).
  A guess about an unexplained thing teaches nothing.
- **Worked example, then rule, then formula.** Walk one case with numbers
  before the general statement; put a formula after the reader has seen
  what each symbol does.
- **Close the loop.** After evidence, say what it answers ("So
  rejections are about fairness, not confusion"), and say what question
  comes next.
- Prefer the book's figure with 2-3 pinned notes over describing it, and
  say how to read it.
- A recall check right after an idea is cheap; one at the end of a section
  that mixes ideas is better.

### Failures seen in real lessons

- **Quizzing an untaught term**: "In a dictator game the responder can't
  reject. What do proposers give?", asked before any slide said what a
  dictator game is.
- **The puzzle with no baseline**: data "against self-interest" before
  the lesson says what self-interest predicts.
- **Unexplained notation**: "(8,2)" with no key; a formula whose
  symbols are introduced after it.
- **Jargon in passing**: "job rent", "market integration", "utility
  function" used as if known.
- **Citation kickers**: "List and Cherry", "Berg, Dickhaut and McCabe"
  as labels tell a reader nothing.
- **Theory soup**: a match of theory names the lesson never taught.
- **The explanation in the wrong place**: the slide shows a number; the
  reason it matters sits in `deeper` or in speaker `notes`.

### Interactions that earn their place

Before keeping an interaction, ask: does the reader have to *use* what the
lesson just taught, and is the answer impossible to guess from the wording?
If not, cut it, even if counts drop. Anti-patterns seen in real lessons:

- **Guess the author's opinion or an arbitrary threshold** ("which cutoff
  does the author use?"): nothing to reason from.
- **The answer is on the previous slide**: recall of what you just read.
- **Keyword matching**: a match/sort where shared words give it away.
- **Click-through steps**: a list split into clicks; make it one figure.
- **Sum-only sliders**: an explorable whose output is just the inputs
  added up; show the rule instead, or make it a tool.
- **Obvious sorts**: every item's tone gives its bucket. Include the
  cases the book distinguishes (a lone complaint vs a consensus).

Good ones: a prediction the reader can work out from the chapter (a
calculation), an explorable on the chapter's formula with the book's
thresholds as verdicts, a sort whose items need the book's distinctions,
and a tool the reader keeps using after the lesson.
- Present mode reads from across a room: if a slide needs more than ~30
  seconds of reading, split it or move detail to `deeper`/`notes`.
