# Bookery

**Turn a PDF book into a fidelity-checked Docusaurus site — and any chapter
of a PDF or EPUB book into an interactive lesson.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](./pyproject.toml)
[![Managed with uv](https://img.shields.io/badge/managed%20with-uv-de5fe9.svg)](https://docs.astral.sh/uv/)

Bookery extracts a long, figure- and equation-heavy PDF into clean,
navigable Markdown — and then proves it didn't lose anything. Every chapter
has to pass an eight-gate verification harness before it counts as done:
text coverage, numeric-token fidelity, structural counts, asset rendering,
math, cross-references, a full site build, and visual adjudication. Nothing
is marked complete on the strength of an LLM's or a human's say-so alone.

The same canonical model also feeds **lessons**: a chapter condensed into a
figure-rich, interactive page (study mode and a slide deck) that Claude
writes and gates check — every number on a slide must exist in the chapter,
every quotation must be verbatim. EPUB books skip extraction entirely and
are ingested straight into that model.

## Contents

- [Why this exists](#why-this-exists)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Interactive lessons](#interactive-lessons)
- [EPUB books](#epub-books)
- [CLI reference](#cli-reference)
- [Project layout](#project-layout)
- [Tests](#tests)
- [Known limitations](#known-limitations)
- [License](#license)

## Why this exists

Converting a long PDF into clean docs is easy to get *approximately* right
and very easy to get *silently* wrong: a dropped sub-caption, a mis-OCR'd
exponent, a table row that quietly merges into a paragraph. Bookery assumes
every one of those failure modes will happen and tries to catch each one
with a check that could have failed, rather than a prose description of
what should have happened.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full design rationale and
a running list of the specific extraction defect classes it guards against.

## How it works

Six stages, each checkpointing its output to disk so a run is resumable and
any single stage can be re-run in isolation:

| Stage | Command | What it does |
| --- | --- | --- |
| 0 — acquire | `bookery acquire` | Parses the table of contents and validates a chapter/part page-boundary map against the rendered pages. |
| 1 — extract | `bookery extract` | Runs [Marker](https://github.com/VikParuchuri/marker) and [Docling](https://github.com/docling-project/docling) independently over each chapter and cross-checks them against each other. |
| 2 — reconcile | `bookery reconcile` | Reconciles both extractors' output — plus the PDF's own text layer, the source of truth for prose — into typed blocks: paragraphs, headings, captions, matrices, equations, lists. |
| 3 — assets | `bookery assets` | Crops and exports figure/table images referenced by the reconciled blocks. |
| 4 — emit | `bookery emit` | Emits Docusaurus-ready Markdown, sidebar/nav config, and the bibliography. |
| — verify | `bookery verify` | Runs the eight-gate harness and writes a per-chapter report + review queue. |

**The central design decision:** text comes from the PDF's text layer;
structure comes from the extractors. Marker and Docling are never trusted
over the text layer for prose — a model that paraphrases is
indistinguishable from a model that transcribes until you diff it — but
where the two extractors *disagree* with each other, that disagreement is
exactly the signal used to flag a block for visual adjudication instead of
resolving it by guesswork.

The eight gates, all of which must pass for a chapter to go green:

| # | Gate | Catches |
| --- | --- | --- |
| 1 | `text_coverage` | Body text dropped between the PDF and the emitted Markdown. |
| 2 | `numeric_fidelity` | A missing/extra/altered number — the check coverage alone won't catch. |
| 3 | `structural_counts` | Figure/table/section/equation counts that don't match the PDF's own. |
| 4 | `assets` | Figures that exist on disk but never actually reach the built HTML as `<img>`. |
| 5 | `math` | Equations that don't render cleanly under KaTeX in strict mode. |
| 6 | `xrefs` | Cross-references ("see Figure 3.2") that resolve to nothing. |
| 7 | `build` | The Docusaurus site failing to build, or building with warnings. |
| 8 | `visual` | Everything else — resolved by comparing rendered page proofs page-by-page. |

## Quick start

Dependencies are managed with [uv](https://docs.astral.sh/uv/). The default
dependency set includes Marker and Docling (both pull in `torch`), since
dual-extraction is the pipeline's core technique — the first `uv sync` will
download several GB of packages and model weights.

```bash
git clone https://github.com/bug-breeder/bookery.git && cd bookery
uv sync --extra dev

uv run bookery build --pdf path/to/your-book.pdf --all   # every stage, every chapter
uv run bookery readme                                    # starter README.md for this book
uv run bookery view                                      # installs JS deps + opens the site
```

That's the whole thing: one command to process every chapter, one to get a
project README, one to look at the result. `bookery build` is acquire (if
needed) → extract → reconcile → assets → emit → verify, chained — it
re-invokes the same stage modules a manual run would, so `uv run python -m
pipeline.stage1_extract --chapter 1 --only docling` still works directly
whenever you need a flag the wrapper doesn't expose yet. `--all` processes
every chapter; pass explicit `--chapter N` (repeatable) only when you
deliberately want to work on one chapter at a time.

Only want one extractor backend? `uv sync --extra dev --extra marker-only`
(or `--extra docling-only`) skips installing the other, and pair it with
`--only marker`/`--only docling` on `extract`/`build`.

## Interactive lessons

A chapter is long and repeats itself; a lesson keeps the findings, the
mechanisms behind them and the one or two examples that make them stick,
shown with the book's own figures and data and figures drawn for the chapter,
and gives the reader things to *do* that use what it just taught (predict a
number, sort cases, explore the chapter's formula, fill in a tool). One page
works two ways: **Study** (scroll at your own pace, the book's passage one
click away from every slide) and **Present** (a 16:9 deck with builds and
speaker notes).

Claude writes the lesson as data — `lessons/chNN/lesson.json` in the book's
root — and a shared runtime (`learn/runtime/`) renders it into one
self-contained HTML page, published as a private Claude Artifact. In Claude
Code the whole loop is one skill:

```
/learn some-book 2           # or: "make chapter 2 into an interactive lesson"
```

which runs, and can be run by hand:

```bash
uv run bookery learn pack   --chapter 2   # the chapter as one tagged Markdown file to author from
uv run bookery learn verify --chapter 2   # gates + build + headless-Chrome self-test
uv run bookery learn shot   --chapter 2 --present --slide offers   # screenshot to look at
uv run bookery learn record --chapter 2 --url https://claude.ai/artifact/...
```

A lesson condenses on purpose, so its gates check the opposite promises to
the site's — not "nothing was lost" but "nothing was made up":

| Gate | Catches |
| --- | --- |
| `schema` | A malformed slide, or a figure/block reference that resolves to nothing. |
| `grounding` | A content slide that doesn't cite the blocks it condenses. |
| `numbers` | A number on a slide that isn't in the chapter (a proportion may appear as its percent). |
| `quotes` | A quotation that isn't verbatim (`...` may join verbatim pieces). |
| `coverage` | A section neither taught nor listed as skipped with a reason the reader sees. |
| `density` | A wall of text: per-slide word budgets, too few figures, no recall check at all. (Interactions have no quota: each one has to earn its place.) |
| `runtime` | Script errors, slides overflowing the stage, broken images, recall checks that give no feedback when answered, horizontal scroll on a phone. |
| `size` | A page too big for an Artifact. |

Claude's own knowledge is allowed only in a slide's `beyond` field, which
renders as "Beyond the book". `.claude/skills/learn/authoring.md` is the full
vocabulary (slide parts, widget types, figure notes, custom widgets).

Lessons quote the book, so they live with the book's data, never in this
repository (`lessons/` is git-ignored here). A published lesson is private
to you; share it only where sharing excerpts of the book is fine.

## EPUB books

An EPUB is already structured text, so it skips stages 0-3:

```bash
BOOKERY_ROOT=~/books/some-book uv run bookery ingest --epub some-book.epub --list   # check the chapter map
BOOKERY_ROOT=~/books/some-book uv run bookery ingest --epub some-book.epub
```

`ingest` reads the package, the spine and the table of contents (EPUB 3 nav,
else EPUB 2 NCX), cuts the spine into chapters at the TOC's entries, and
walks each chapter's XHTML into the same `work/reconcile/chNN.json` blocks the
PDF pipeline writes: headings, paragraphs, lists, tables, footnotes, MathML
and TeX as `$…$`, figures exported to `site/static/img/` with their captions
and "Figure 3.2" labels. Pages come from the book's print page markers when
it has them; without markers, blocks carry no page.

Chapters keep the book's own numbers ("Chapter 3", "3. Sorting", "Three",
"III"). A lone Introduction or Preface before chapter 1 becomes chapter 0,
front and back matter (contents, index, notes, acknowledgements…) is
skipped, and part dividers are descended through. Always read `--list`
first: when a book's TOC numbering is odd, `--renumber` numbers every kept
entry 1..N instead.

## CLI reference

```
bookery acquire   --pdf FILE
bookery ingest    --epub FILE [--list] [--renumber] [--force]   # instead of stages 0-3
bookery extract   [--chapter N ... | --all] [--pdf FILE] [--force] [--only marker|docling]
bookery reconcile [--chapter N ... | --all] [--pdf FILE]
bookery assets    [--chapter N ... | --all] [--pdf FILE]
bookery emit      [--chapter N ... | --all] [--pdf FILE] [--skip-bibliography]
bookery verify    [--chapter N ... | --all] [--build]
bookery status                                   # regenerate PROGRESS.md
bookery readme    [--force]                      # starter README.md for this book
bookery view      [--build] [--reinstall] [--port N]   # the one command to look at it
bookery build     [--chapter N ... | --all] [--pdf FILE] [--force] [--only ...]
                  [--skip-bibliography] [--skip-verify] [--site-build]

bookery learn pack   [--chapter N ... | --all]
bookery learn build  [--chapter N ... | --all]
bookery learn verify [--chapter N ... | --all] [--no-browser]
bookery learn shot   --chapter N [--slide ID] [--present] [--dark] [--phone]
bookery learn record --chapter N --url URL
```

`bookery view` detects whichever package manager matches `site/`'s lockfile
(npm/yarn/pnpm), installs JS dependencies on first run (or with
`--reinstall`), and starts the Docusaurus dev server. Pass `--build` for a
production build served as static files instead.

`--chapter` is repeatable; `--all` resolves every chapter from
`work/triage.json` (i.e. whatever `acquire` found). Note that `emit` always
needs *every* chapter you want in the final site, not just a new one — it
regenerates the sidebar, home page, and cross-reference registry from
exactly the set it's given each time.

Run `uv run bookery <command> --help` for the full flag list on any
subcommand.

## Project layout

```
pipeline/           stages 0-4 (see "How it works" above)
  cli.py            the `bookery` command
  config.py         shared paths/constants; the only place stage paths live
  epub_ingest.py    `bookery ingest`: an EPUB straight into the canonical model
verify/             the eight-gate harness + independent reference-text builder
learn/              `bookery learn`: pack, lesson gates, build, browser self-test
  runtime/          the lesson page's JS/CSS, inlined into every built lesson
.claude/skills/learn/  the `/learn` skill and its authoring reference
site/               Docusaurus v3 scaffold, pre-wired for this pipeline's output
  docs/             generated by `emit` — not checked into this repo
  static/img/       generated by `assets` — not checked into this repo
tests/              pytest suite (pure-function unit tests, no PDF required)
scripts/            one-off shell helpers (fetching a source PDF, etc.)
work/               per-run intermediate artifacts (git-ignored)
reports/            per-chapter gate reports + review queue (git-ignored)
ARCHITECTURE.md     design decisions and extraction pitfalls, read this next
```

`site/` ships without any `docs/` content or figure images — stage 4 and
stage 3 populate those directories once you run the pipeline against your
own PDF. Nothing under `work/`, `reports/`, `site/docs/`, or
`site/static/img/` is committed to this repository (see `.gitignore`),
so it stays free of any specific book's content.

### Running against a book that lives in its own repo

By default every path in `pipeline/config.py` (`pdf/`, `work/`, `site/`,
`reports/`) is resolved relative to wherever this checkout of the tool
lives, since that's what a self-contained clone-per-book setup needs. Set
`BOOKERY_ROOT` to point the tool at a book's data living somewhere else
entirely — a separate "book store" repo with no copy of the pipeline code
in it — instead:

```bash
BOOKERY_ROOT=/path/to/book-store/some-book uv run bookery verify --all
```

`some-book/` just needs the same `pdf/`, `work/`, `site/`, `reports/`
layout this repo has (plus `epub/` for an ingested EPUB and `lessons/` for
lessons); nothing under it needs to know `bookery` itself exists anywhere in
particular.

## Tests

```bash
uv run pytest
```

Covers the pure text-normalisation functions in `pipeline/textnorm.py` with
synthetic inputs: accent repair, ligature/quote/dash normalisation,
numeric-token extraction, hyphenation rejoining, and the figure-vs-prose
"integer soup" heuristic. `tests/test_epub_ingest.py` builds small EPUBs on
the fly (nav and NCX, page markers, MathML, captions, mixed inline content)
and ingests them end to end; `tests/test_learn.py` runs the lesson gates and
the build payload against a synthetic chapter. There are no book fixtures
bundled — add your own regression tests against your book's actual
extraction output as you find defects, following these files' pattern.

## Known limitations

- Marker runs in fast mode with no LLM pass; equation-heavy content is the
  hardest case for it.
- Docling's layout model is pinned to CPU — its float64 tensors aren't
  supported on Apple Silicon's MPS backend.
- The scanned-PDF path, where model text would need to become primary
  instead of the text layer, isn't exercised by this pipeline's own tests.
- An indented list distinguished only by layout (not by a recurring marker
  pattern) reconciles as one flowing paragraph rather than separate list
  lines. No content is lost — every gate stays token- and number-exact —
  but it reads differently than the source's one-line-per-item layout.
- An ingested EPUB feeds `bookery learn`; the site path (`emit`, `verify`)
  hasn't been exercised against one.
- EPUB chapter maps come from heuristics over the TOC. Odd books (a single
  XHTML file, a flat TOC of 100+ entries, a 3-in-1 omnibus) can map
  imperfectly — `--list` shows the map before anything is written, and
  `--renumber` is the escape hatch.
- An EPUB's SVG figures aren't exported (only raster images), and an EPUB
  without print page markers gives lessons no page numbers to cite.
- The lesson runtime gate needs Chrome, Chromium or Edge installed; without
  one it reports BLOCKED, never PASS.

## License

MIT — see [LICENSE](./LICENSE). This repository contains no book content,
only the pipeline, the lesson runtime and the verification tooling.
