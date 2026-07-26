# Bookery

**Turn a PDF book into a fidelity-checked Docusaurus site.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](./pyproject.toml)
[![Managed with uv](https://img.shields.io/badge/managed%20with-uv-de5fe9.svg)](https://docs.astral.sh/uv/)

Bookery extracts a long, figure- and equation-heavy PDF into clean,
navigable Markdown — and then proves it didn't lose anything. Every chapter
has to pass an eight-gate verification harness before it counts as done:
text coverage, numeric-token fidelity, structural counts, asset rendering,
math, cross-references, a full site build, and visual adjudication. Nothing
is marked complete on the strength of an LLM's or a human's say-so alone.

## Contents

- [Why this exists](#why-this-exists)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
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

uv run bookery build --pdf path/to/your-book.pdf --all
uv run bookery verify --all --build

cd site && yarn install && yarn start
```

`bookery build` is the whole pipeline in one command: acquire (if needed) →
extract → reconcile → assets → emit → verify, for whichever chapters you ask
for. It re-invokes the same stage modules a manual run would — it's a
convenience wrapper, not a reimplementation — so `uv run python -m
pipeline.stage1_extract --chapter 1 --only docling` still works directly
whenever you need a flag the wrapper doesn't expose yet.

Only want one extractor backend? `uv sync --extra dev --extra marker-only`
(or `--extra docling-only`) skips installing the other, and pair it with
`--only marker`/`--only docling` on `extract`/`build`.

## CLI reference

```
bookery acquire   --pdf FILE
bookery extract   [--chapter N ... | --all] [--pdf FILE] [--force] [--only marker|docling]
bookery reconcile [--chapter N ... | --all] [--pdf FILE]
bookery assets    [--chapter N ... | --all] [--pdf FILE]
bookery emit      [--chapter N ... | --all] [--pdf FILE] [--skip-bibliography]
bookery verify    [--chapter N ... | --all] [--build]
bookery status                                   # regenerate PROGRESS.md
bookery build     [--chapter N ... | --all] [--pdf FILE] [--force] [--only ...]
                  [--skip-bibliography] [--skip-verify] [--site-build]
```

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
verify/             the eight-gate harness + independent reference-text builder
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

## Tests

```bash
uv run pytest
```

Covers the pure text-normalisation functions in `pipeline/textnorm.py` with
synthetic inputs: accent repair, ligature/quote/dash normalisation,
numeric-token extraction, hyphenation rejoining, and the figure-vs-prose
"integer soup" heuristic. There are no PDF fixtures bundled — add your own
regression tests against your book's actual extraction output as you find
defects, following this file's pattern.

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

## License

MIT — see [LICENSE](./LICENSE). This repository contains no book content,
only the pipeline and verification tooling.
