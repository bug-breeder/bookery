# Bookery

Bookery turns a large PDF book into a fidelity-checked Docusaurus v3 site.

"Fidelity-checked" means every chapter must pass an eight-gate verification
harness before it's considered done — coverage, numeric-token, structural,
KaTeX, and visual-diff checks that compare the emitted Markdown back against
independently reconstructed reference text extracted from the source PDF.
Nothing is marked complete on the strength of an LLM's or a human's say-so
alone.

## Why this exists

Converting a long, figure- and equation-heavy PDF into clean docs is easy to
get *approximately* right and very easy to get *silently* wrong: a dropped
sub-caption, a mis-OCR'd exponent, a table row that merges into a paragraph.
This pipeline assumes every one of those failure modes will happen and tries
to catch it with a check that could have failed, rather than a prose
description of what should have happened. See [ARCHITECTURE.md](./ARCHITECTURE.md)
for the design decisions and the specific defect classes it guards against.

## Pipeline stages

| Stage | Module | Responsibility |
| --- | --- | --- |
| 0 | `pipeline/stage0_acquire.py` | Parse the table of contents and validate a chapter/part page-boundary map against the rendered pages. |
| 1 | `pipeline/stage1_extract.py` | Dual extraction: [Marker](https://github.com/VikParuchuri/marker) and [Docling](https://github.com/docling-project/docling) run independently over each chapter and are cross-checked against each other. |
| 2 | `pipeline/stage2_reconcile.py` | Reconcile the two extractors' output — plus the PDF's own text layer, which is the source of truth for prose — into typed blocks (paragraphs, headings, captions, matrices, equations, lists). |
| 3 | `pipeline/stage3_assets.py` | Crop and export figure/table images referenced by reconciled blocks. |
| 4 | `pipeline/stage4_emit.py` | Emit Docusaurus-ready Markdown + sidebar/nav config. |
| — | `verify/` | Independent reference-text reconstruction and the eight-gate verification runner (`verify/runner.py`). |

## Getting started

Dependencies are managed with [uv](https://docs.astral.sh/uv/). The default
dependency set includes Marker and Docling (both pull in `torch`), since dual
extraction is the pipeline's core technique — the first `uv sync` will
download several GB of packages and model weights.

```bash
uv sync --extra dev   # or: --extra dev --extra marker-only / --extra docling-only,
                       # if you only want one extractor and plan to run
                       # `--only marker` / `--only docling` everywhere below

uv run bookery build --pdf path/to/your-book.pdf --all
uv run bookery verify --all --build

cd site && yarn install && yarn start
```

`bookery build` is the whole pipeline in one command: acquire (if needed) →
extract → reconcile → assets → emit → verify, for the chapter(s) you ask
for. It re-invokes the same stage modules a manual run would, so it's just
the convenient path — see below for running stages individually.

### Running stages individually

Useful when iterating on one stage without re-running everything upstream,
since each stage's output is cached to `work/` and skipped on the next
run unless you pass `--force` (stage 1) or change the input.

```bash
uv run bookery acquire --pdf path/to/your-book.pdf
uv run bookery extract --chapter 1        # or --all
uv run bookery reconcile --chapter 1
uv run bookery assets --chapter 1
uv run bookery emit --all                 # always needs every chapter, see ARCHITECTURE.md
uv run bookery verify --chapter 1 --build
uv run bookery status                     # regenerate PROGRESS.md from the gate reports
```

Each of these is a thin wrapper around the underlying stage module, which is
still directly runnable if you need a flag `bookery` doesn't expose yet, e.g.
`uv run python -m pipeline.stage1_extract --chapter 1 --only docling`.

`site/` is a Docusaurus v3 scaffold pre-wired for this pipeline's output
(math via `remark-math`/`rehype-katex`, `onBrokenLinks: 'throw'` so a lost
cross-reference fails the build instead of warning). It ships without any
`docs/` content — stage 4 populates that directory when you run it against
your own PDF.

## Tests

```bash
uv run pytest
```

`tests/` covers the pure text-normalisation functions (`pipeline/textnorm.py`)
with synthetic inputs — accent repair, ligature/quote/dash normalisation,
numeric-token extraction, hyphenation rejoining, and the figure-vs-prose
"integer soup" heuristic. It intentionally doesn't ship fixtures derived from
any specific PDF; add your own regression tests against your book's actual
extraction output as you find defects, following this file's pattern.

## License

MIT. See [LICENSE](./LICENSE). This repository contains no book content —
only the pipeline and verification tooling.
