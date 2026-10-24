# Architecture

How the pipeline is put together, the decisions behind it, and the failure
modes it was built to catch. Read this before changing a stage.

## Pipeline stages

Six stages, each checkpointing to disk so work is resumable and inspectable
without re-running everything upstream:

| Stage | Module | Output |
| --- | --- | --- |
| 0 | `pipeline/stage0_acquire.py` | `work/triage.json` — chapter/part boundary map |
| 1 | `pipeline/stage1_extract.py` | `work/extract/{marker,docling}/chNN.json` |
| 2 | `pipeline/stage2_reconcile.py` | `work/reconcile/chNN.json` — canonical model |
| 3 | `pipeline/stage3_assets.py` | figure crops + `work/assets.json` |
| 4 | `pipeline/stage4_emit.py` | `site/docs/**`, `sidebars.ts` |
| — | `verify/runner.py` | `reports/chNN.json`, `reports/review-queue.md` |
| 0-3 (EPUB) | `pipeline/epub_ingest.py` | `work/triage.json`, `work/reconcile/chNN.json`, `work/assets.json` |
| lesson | `learn/` | `lessons/chNN/lesson.json` → `work/learn/chNN/index.html`, `reports/learn-chNN.json` |

Eight gates, all of which must pass for a chapter to be green: text
coverage, numeric fidelity, structural counts, assets, math, cross-references,
site build, visual adjudication. See `verify/gates.py` and `verify/runner.py`.

## The central design decision

**Text comes from the PDF text layer; structure comes from the extractors.**
Marker and Docling run independently over the same pages and are used as
structural sources *and* as validators of each other. Where they disagree,
the block is flagged for visual adjudication rather than resolved by
guesswork. Their text is never trusted over the text layer, because a model
that paraphrases is indistinguishable from a model that transcribes until you
diff it.

For a scanned PDF with no reliable text layer this inverts and model text
becomes primary. That path is not exercised by the pipeline's own test
fixtures.

## Conventions that must not change silently

- **Chapter bodies are `.md`, not `.mdx`.** Book prose is routinely full of
  bare `<`, `{` and `}`; each is a build error under MDX. `markdown.format:
  'detect'` keeps chapters CommonMark while still running the remark math
  plugins.
- **Doc ids carry the chapter number** (e.g. `id: 02-some-title` in front
  matter). Docusaurus strips a leading `NN-` from filenames, which would drop
  the number from the route and break every cross-reference.
- **`sidebars.ts`, `docs/index.md` and the bibliography are generated.**
  Never hand-edit; regenerate with `stage4_emit` (or `bookery emit`).
- **`onBrokenLinks`, `onBrokenAnchors`, `onBrokenMarkdownLinks` are all
  `throw`.** A broken link is a lost cross-reference, not a warning to
  tolerate.
- **Front matter (preface/introduction) is modelled as chapter 0** rather
  than a bespoke path, so it gets the exact same stage1-4 and gate machinery
  as every numbered chapter for free. It's injected *after* the
  printed-page-to-PDF-page offset invariant is checked against the numbered
  chapters, since front matter is commonly paginated with its own sequence
  (e.g. lowercase roman numerals) rather than the body's.
- **`stage4_emit` must be given every chapter that should exist in the
  site**, not just the newest one — it regenerates the sidebar, home page,
  and   cross-reference registry from the full emitted set each time. `bookery
  emit --all` handles this for you; calling the raw stage module for a
  single chapter will silently drop every other chapter from the nav.

## EPUB ingest

An EPUB is already structured text, so it skips extraction and
reconciliation: `pipeline/epub_ingest.py` writes the canonical model
directly, with `source.format = "epub"` in `triage.json` and
`source: "epub"` on every block. Anything that reads `work/reconcile` reads
both kinds of book the same way.

**The chapter map is the hard part**, because a TOC says nothing reliable
about which entries are chapters. In order:

- Front and back matter is skipped by label (contents, index, notes,
  acknowledgements, "Also by", Vietnamese "mục lục", per-page entries…).
- A part is an *unnumbered* entry whose children are mostly numbered and
  span at least two spine documents; ingest descends into it. A chapter
  whose sections are numbered ("1.1", "1.2" in the same file) has numbered
  children too, so "spans two documents" is what tells the two apart.
- A chapter number is accepted only as part of an increasing sequence with
  small steps, so a title that merely starts with a number ("20 Percent
  Time") isn't chapter 20. A restart (an omnibus's second "Chapter 1")
  ends the sequence and is reported as a note.
- When fewer than two numbers, or under 30% of kept entries, are accepted,
  the book is numbered sequentially instead. `--renumber` forces that.
- The last opener (Preface, Introduction…) before chapter 1 becomes chapter
  0, matching the PDF path. Unnumbered entries between chapters fold into the
  previous chapter; trailing ones (Conclusion, Epilogue) continue the
  sequence.

A chapter runs from its entry's (document, anchor) to the next *boundary*,
and skipped entries are boundaries too, so back matter never leaks into the
last chapter. Converters (calibre especially) often split files and drop
the anchors the TOC points at: ingest then looks for the id in the other
spine documents, then for a heading with the entry's text, and drops a
chapter that would start where an earlier one starts (with a note) rather
than emit the same blocks twice.

**Parse as XML first.** The HTML parser reads a self-closing `<span/>` as
an open tag, so one empty page marker swallows the rest of the page into
itself. Documents are parsed strictly (HTML named entities rewritten to
numeric references, entity resolution and network access off) and fall
back to the HTML parser only when that fails.

**Walking blocks.** Elements are walked in reading order. A container that
mixes text with block children yields the inline runs between them as
paragraphs, so text after the first inline child isn't lost. Text collection
pads a space at every block boundary, so adjacent cells or spans ("4.1" and
"Perfect") don't fuse. Headings come from tags and from the publisher class
names that stand in for them (`CT`, `H1`, `chapterHeadA`…). Captions come
from classes and from a "Figure 3.2" / "Table 2.1" label, and are bound to
the nearest image. MathML becomes TeX through `alttext` or a TeX
annotation, and an inline image whose alt text is TeX is kept as `$…$`. An
equation that exists only as an image is flagged rather than given a fake
`latex`. When a chapter's text lacks headings for its TOC sub-entries, they
are inserted, so lessons still see its sections.

**One file per TOC entry.** Exports like Vellum's give every section its
own file, each starting again at `<h1>`. A heading the TOC names takes the
TOC's depth; the headings after it, in the same file, nest under it,
keeping their steps relative to its own tag level. Without this every
section's subheads would land at the top level and a chapter would show
dozens of "sections".

**Noise the text doesn't need.** Note references (`epub:type="noteref"`,
`doc-noteref`, a `<sup>` holding just an in-book link like "1" or "†") are
dropped, so "lines" doesn't become "lines1"; superscripts that aren't
links (r<sup>2</sup>) stay. An image the book uses three or more times
(a quote mark, a chapter ornament) isn't a figure unless it has a caption,
and neither is an uncaptioned image that is only a thin horizontal stroke
(a section-break flourish).

**Pages.** `epub:type="pagebreak"` markers set each block's `page`. The
triage entry's `printed_page` equals its `pdf_page`, so the page offset is 0
and every page-aware reader works unchanged. A book without markers has
`page: null` blocks and no page numbers anywhere downstream. Nothing
invents them.

## Lessons

`learn/` turns one chapter into a condensed, interactive page published as
a Claude Artifact: pack → `lesson.json` (written by Claude, see
`.claude/skills/learn/`) → gates → build → browser self-test → publish →
`bookery learn record`.

**Claude writes data, not a page.** A lesson is JSON (slides with one main
part each: a figure, a widget, stats, a quote, columns) rendered by one
shared runtime (`learn/runtime/app.js`, `app.css`). Every lesson gets the
same tested present mode, study mode, themes, keyboard handling and
self-test, and the authoring effort goes into the teaching rather than into
CSS. Figures are the exception: the best picture for an idea is usually
specific to it, so a lesson draws its figures as `custom` widgets in
`lessons/chNN/widgets/NAME.js` (code written for that chapter), and uses the
built-in chart/table widgets only where they really are the best fit. A
custom widget marked `"static": true` is a figure (visual, not "your
turn").

**What a lesson may say comes only from the canonical model.**
`learn/source.py` is the read-side view of it: the blocks (the only text a
lesson may quote or take numbers from), the section map (numbered "3.1"
headings, else the second-shallowest unnumbered heading level as `s1`,
`s2`…), and the exhibits (`fig 3.1` / `tbl 3.1`, or a block id when
unlabelled).

**The gates check the inverse of the site's promise.** A site must lose
nothing; a lesson loses most of the chapter on purpose and must *make up*
nothing:

- `numbers`: every numeric token in reader-visible text must occur in the
  chapter, where a proportion matches its percent (0.45 ↔ 45). A quiz
  distractor's text is exempt (it is there to be wrong), but its `why` is a
  claim and is checked.
- `quotes`: verbatim after normalisation; `...` splits a quote into
  pieces, and each must be verbatim.
- `coverage`: a section is taught when a slide cites one of its blocks or
  shows one of its exhibits; otherwise it must be listed in `skipped` with
  a reason, which the page shows the reader.
- `density`: per-field word budgets per slide, plus lesson-wide minimums for
  visual slides and the book's exhibits, and at least one recall check. It
  deliberately sets no interaction quota: a quota is what filled early
  lessons with checks that taught nothing.
- Numbers a widget read off a book image (a screenshot of a spreadsheet)
  can't be checked against the text; the widget names those images in
  `transcribed`, `numbers` lists the values for review instead of failing
  them, and the page shows the images beside the widget.
- `runtime`: headless Chrome loads `preview.html#selftest`, which mounts
  every slide in both modes, answers every recall check the way a reader
  would, and reports script errors, overflow of the 1600×900 stage, broken
  images, checks that give no feedback, and horizontal scroll at phone
  width. With no browser installed the gate is BLOCKED, never PASS.

Outside knowledge is allowed only in a slide's `beyond` field, rendered as
"Beyond the book", so a reader can always tell the book's claims from
Claude's.

**The build fits the Artifact sandbox.** The page is one file: figures
re-encoded as WebP data URIs (at most 1400px wide), the cited passages and
the runtime inlined. The only network loads are Google Fonts and, when a
lesson has math, KaTeX from cdnjs, the hosts the Artifact CSP allows.
`index.html` has no document skeleton (the Artifact wraps it), and
`preview.html` adds one for local viewing and the self-test.

**Lessons live with the book's data**, never in this repository: they
quote the book. `lessons/manifest.json` in the book root records each
chapter's Artifact URL, so a rebuilt lesson is republished to the same link.

## Known extraction pitfalls

Each of these was a real defect found while running this pipeline against a
real book, with a regression test in `tests/test_artifacts.py` guarding it.
Several were invisible to the coverage and numeric gates because **both
sides of the comparison were wrong in the same way** — that's the failure
mode to fear most; a gate that agrees with a wrong reference still passes.

- **Captions can run to several lines** and only the first carries the
  `Figure N.N:` label. Caption extent has to come from the extractors'
  caption bounding boxes; without it, the remainder gets emitted as loose
  body prose.
- **Figure bounding boxes must be clipped above their caption.** Extractor
  picture regions sometimes swallow the caption text, which then counts as
  figure interior and silently drops from both the emitted page *and* the
  independently-built reference — no gate can see a drop that happens on
  both sides at once. Sub-captions inside the drawing itself (multi-panel
  figures) should be left in place; clipping to them cuts the figure apart.
- **Brackets in alt text silently break images.** A caption carrying a
  bracketed citation marker (e.g. `[12]`) ends the Markdown image syntax
  early if copied verbatim into alt text, and the figure renders as literal
  prose while the file sits happily on disk. Verify that figures actually
  reach the built HTML as `<img>` elements, not just that the file exists.
- **Small caps must be restyled, not rewritten.** A word set in a small-caps
  font extracts as literal lowercase/uppercase text depending on the
  extractor; naively re-casing it introduces characters the source doesn't
  contain and breaks coverage. Detect small caps by font metadata and wrap
  in a span instead of transforming the string.
- **A lettered list marker like `(a)` is ambiguous** between a figure
  sub-caption (small font) and a numbered sub-part of a larger list/exercise
  (body-text size). Font size, not the marker text, disambiguates them.
- **Never indent emitted prose with four spaces** if the target renderer
  treats that as a code fence (Markdown/CommonMark does). Use an inline or
  block span class instead, which also keeps nested Markdown links working —
  raw HTML block elements often don't parse their contents as Markdown.
- **Detect running-head/footer furniture by positional slot occupancy**, not
  by text recurrence — page-local headers can vary too much per-page to
  threshold on frequency alone.
- **A footnote-size heuristic needs both small font size and page-bottom
  position.** Size alone will also match small in-figure labels.
- **Dehyphenation must use one attested-forms set built from the whole
  document**, not decided per page — whether a line-wrapped hyphenated
  compound keeps its hyphen depends on how it's spelled elsewhere in the
  same document.
- **Ligatures (`ﬁ`, `ﬀ`, etc.) must be expanded on output**, or full-text
  search against the emitted content silently breaks.
- **A ligature's implicit space is not a real character in the text
  layer.** Some PDF producers encode the space after a ligature as pure
  glyph positioning with no character in the stream, so consecutive words
  fuse (`payoffmatrix` where the page reads "payoff matrix"). Because a
  naively-built reference text is extracted from the same text layer, *every
  gate can agree with itself and still be wrong* — this class of bug is
  caught by reading the rendered page, not by a gate, unless lines are
  reassembled from raw glyph positions with an explicit gap threshold.
- **Multi-line section headings must be accumulated like captions**, with
  the label parsed from the joined text — emitting only the heading's first
  line orphans the rest as body prose (and can split a hyphenated word
  across the line break).
- **Tables drawn with vector rules rather than a PDF table object** (common
  for hand-typeset matrices/grids in LaTeX) are reported as neither a table
  nor a picture by either extractor; they have to be recovered from raw
  glyph geometry. A single-cell "table" is a legitimate edge case, not
  necessarily a parsing failure — accept it only when a caption sits
  directly below the recovered grid, to avoid false positives on stray
  aligned numbers.
- **A structural element rendered as a Markdown table still needs an entry
  in whatever "does every figure/table have an asset" gate exists** — track
  which labels were deliberately emitted as text/tables rather than images so
  the assets gate doesn't demand a PNG for content that was never meant to be
  one.
- **Bare decimals and ordinals must be tokenizable as numbers.** A number
  regex that requires a leading digit misses `.48`; one that excludes digits
  followed by letters misses `501st`. Both are invisible to a numeric
  fidelity gate that doesn't tokenize them as numbers on either side.
- **A cross-extractor numeric disagreement check should be whole-chapter,
  not per-page**, and one-directional. Per-page comparison produces false
  positives on every paragraph straddling a page break (extractors attribute
  the whole paragraph to one page); and "the extractor saw a number we
  don't have" is the only direction that's actual evidence of a problem — the
  reverse just means the extractor dropped something it commonly drops
  (an equation, a list marker), not that a number was fabricated.
- **Count extractor items, not their internal provenance regions**, when
  comparing structural counts — some extractors emit one region per
  page-break fragment of the same logical paragraph/list item, which
  inflates the extractor's side of a count comparison and invents
  disagreements that aren't real.
- **A gate must not confuse "ran and found zero" with "did not run".** A
  chapter that legitimately has zero figures (or zero equations, or zero of
  whatever a gate checks) must still be able to reach PASS; block only on
  the *absence* of the stage's output artifact, never on an empty list from
  a stage that ran correctly.
- **A structural element with no fixed reading order (e.g. a 2D grid/table)
  breaks a linear order-check.** A grid printed one way in the source and
  linearized another way in the output will show a spurious diff under any
  check that compares token *sequence*; verify multiset content and
  structural placement (e.g. "does each cell sit on the right row/column
  header") separately instead of forcing it through the same order-sensitive
  check used for prose.

## Verification philosophy

- A gate that has never failed has not been tested. When a gate passes on
  content that lacks the relevant feature entirely, say so rather than
  silently counting it as a pass.
- Prefer a deterministic check to an eyeball. A per-page token-*sequence*
  comparison against the source catches reordering and duplication that a
  chapter-level multiset coverage check tolerates; spend visual review on
  what automation is weak at (typically figures), not on what it's strong at
  (text).
- Adjudication verdicts should be durable, attributable records (who/what
  reviewed, and what was actually checked) — never record a verdict for
  something that wasn't actually inspected.
- Fixing the pipeline is not enough; fix the *gate* that failed to catch the
  defect in the first place. A defect found by eye instead of by an
  automated check means the harness has a hole.
