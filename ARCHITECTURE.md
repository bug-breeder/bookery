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
