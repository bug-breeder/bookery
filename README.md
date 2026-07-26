# Bookery

Bookery turns a large PDF book into a fidelity-checked Docusaurus v3 site.
It was built and battle-tested against an 833-page, 25-chapter academic
textbook, but the pipeline itself is domain-agnostic: nothing in this
repository is specific to that book, and no book content ships here.

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
description of what should have happened.

## Pipeline stages

| Stage | Module | Responsibility |
| --- | --- | --- |
| 0 | `pipeline/stage0_acquire.py` | Fetch/validate the source PDF, split into per-chapter page ranges. |
| 1 | `pipeline/stage1_extract.py` | Extract raw text/layout per page (PyMuPDF, with optional Marker/Docling backends for harder layouts). |
| 2 | `pipeline/stage2_reconcile.py` | Reconcile extractor output into typed blocks (paragraphs, headings, captions, matrices, equations, lists). |
| 3 | `pipeline/stage3_assets.py` | Crop and export figure/table images referenced by reconciled blocks. |
| 4 | `pipeline/stage4_emit.py` | Emit Docusaurus-ready Markdown + sidebar/nav config. |
| — | `verify/` | Independent reference-text reconstruction and the eight-gate verification runner (`verify/runner.py`). |

Run `python -m pipeline.status` after any stage to regenerate a progress
report from the on-disk gate results.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m pipeline.stage0_acquire --pdf path/to/your-book.pdf
python -m pipeline.stage1_extract --chapter 1
python -m pipeline.stage2_reconcile --chapter 1
python -m pipeline.stage3_assets --chapter 1
python -m pipeline.stage4_emit --chapter 1
python -m verify.runner --build --chapter 1

cd site && yarn install && yarn start
```

`site/` is a Docusaurus v3 scaffold pre-wired for this pipeline's output
(math via `remark-math`/`rehype-katex`, `onBrokenLinks: 'throw'` so a lost
cross-reference fails the build instead of warning). It ships without any
`docs/` content — stage 4 populates that directory when you run it against
your own PDF.

## Optional heavy extraction backends

`pipeline/stage1_extract.py` can fall back to
[Marker](https://github.com/VikParuchuri/marker) or
[Docling](https://github.com/docling-project/docling) for pages the default
PyMuPDF text-layer extraction handles poorly (e.g. scanned pages). These are
optional, heavy (`torch`-based) dependencies — install them only if you need
that path; see the lazy imports in `stage1_extract.py`.

## Tests

```bash
pytest
```

`tests/test_artifacts.py` asserts structural/content invariants against
pipeline output fixtures. It expects you to have already run the pipeline
end-to-end on a PDF of your own to generate those fixtures; no fixture data
is bundled here, since none of it is checked into this repository.

## License

MIT. See [LICENSE](./LICENSE). This repository contains no book content —
only the pipeline and verification tooling.
