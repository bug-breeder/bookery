"""Stage 1 - dual extraction.

Marker and Docling are run independently over the same page range. Neither
sees the other's output. Where they disagree is the pipeline's best available
signal for "a human needs to look at this page", which is what Stage 2 acts
on.

Both extractors load large models, so a single process handles a batch of
chapters and checkpoints each chapter to disk as it finishes. Re-running
skips chapters that already have output unless --force is given.

Run:  python -m pipeline.stage1_extract --chapter 2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

from . import config

MARKER_DIR = config.EXTRACT_DIR / "marker"
DOCLING_DIR = config.EXTRACT_DIR / "docling"

# Marker's balanced mode needs a llama.cpp backend that is not usable on this
# machine (Surya's grammar is rejected and pages come back empty). Fast mode
# is what actually runs, and the manifest records the run as degraded.
MARKER_MODE = "fast"
DEGRADED_REASON = (
    "marker running in 'fast' mode without --use_llm: no LLM service available "
    "(no API key, and the local llama.cpp backend is incompatible with Surya's "
    "grammar). Equation and table refinement is therefore unassisted."
)


def _seed_everything() -> None:
    random.seed(config.SEED)
    os.environ.setdefault("PYTHONHASHSEED", str(config.SEED))
    try:
        import numpy as np

        np.random.seed(config.SEED)
    except Exception:
        pass
    try:
        import torch

        torch.manual_seed(config.SEED)
    except Exception:
        pass


def load_triage() -> dict:
    if not config.TRIAGE_JSON.exists():
        raise SystemExit("FATAL: work/triage.json missing. Run stage0 first.")
    return json.loads(config.TRIAGE_JSON.read_text())


def chapter_range(triage: dict, number: int) -> tuple[int, int]:
    for ch in triage["boundary_map"]["chapters"]:
        if ch["number"] == number:
            return ch["pdf_page"], ch["pdf_page_end"]
    raise SystemExit(f"FATAL: chapter {number} not in boundary map")


# --------------------------------------------------------------------------
# Marker
# --------------------------------------------------------------------------


class MarkerRunner:
    """Holds the Marker model set so it is loaded once per process."""

    def __init__(self) -> None:
        from marker.models import create_model_dict

        self._artifacts = create_model_dict()

    def run(self, pdf: Path, first: int, last: int) -> dict:
        from marker.config.parser import ConfigParser
        from marker.converters.pdf import PdfConverter

        # Marker page ranges are 0-indexed; the boundary map is 1-indexed.
        page_range = f"{first - 1}-{last - 1}"
        settings = {
            "output_format": "json",
            "page_range": page_range,
            "mode": MARKER_MODE,
            "disable_multiprocessing": True,
        }
        parser = ConfigParser(settings)
        converter = PdfConverter(
            config=parser.generate_config_dict(),
            artifact_dict=self._artifacts,
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
        )
        rendered = converter(str(pdf))
        return json.loads(rendered.model_dump_json())


# --------------------------------------------------------------------------
# Docling
# --------------------------------------------------------------------------


class DoclingRunner:
    """Docling, pinned to CPU.

    Its layout model uses float64, which MPS does not implement, so on Apple
    silicon the GPU path fails for every page. CPU is slower but correct, and
    Docling's role here is the semantic second opinion rather than throughput.
    """

    def __init__(self) -> None:
        from docling.datamodel.accelerator_options import (
            AcceleratorDevice,
            AcceleratorOptions,
        )
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions()
        options.accelerator_options = AcceleratorOptions(
            device=AcceleratorDevice.CPU, num_threads=8
        )
        options.generate_page_images = False
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    def run(self, pdf: Path, first: int, last: int) -> dict:
        # Docling page ranges are 1-indexed and inclusive.
        result = self._converter.convert(str(pdf), page_range=(first, last))
        return result.document.export_to_dict()


# --------------------------------------------------------------------------


def extract_chapter(
    number: int,
    pdf: Path,
    first: int,
    last: int,
    marker: MarkerRunner | None,
    docling: DoclingRunner | None,
    force: bool,
) -> dict:
    cid = config.chapter_id(number)
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    DOCLING_DIR.mkdir(parents=True, exist_ok=True)

    summary: dict = {"chapter": number, "pages": [first, last]}

    marker_path = MARKER_DIR / f"{cid}.json"
    if marker_path.exists() and not force:
        summary["marker"] = "cached"
    elif marker is not None:
        started = time.time()
        payload = marker.run(pdf, first, last)
        marker_path.write_text(
            json.dumps(
                {
                    "chapter": number,
                    "pages": [first, last],
                    "mode": MARKER_MODE,
                    "use_llm": False,
                    "degraded": True,
                    "degraded_reason": DEGRADED_REASON,
                    "seconds": round(time.time() - started, 1),
                    "document": payload,
                },
                indent=1,
            )
        )
        summary["marker"] = f"{time.time() - started:.0f}s"

    docling_path = DOCLING_DIR / f"{cid}.json"
    if docling_path.exists() and not force:
        summary["docling"] = "cached"
    elif docling is not None:
        started = time.time()
        payload = docling.run(pdf, first, last)
        docling_path.write_text(
            json.dumps(
                {
                    "chapter": number,
                    "pages": [first, last],
                    "seconds": round(time.time() - started, 1),
                    "document": payload,
                },
                indent=1,
            )
        )
        summary["docling"] = f"{time.time() - started:.0f}s"

    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 1: dual extraction")
    ap.add_argument("--chapter", type=int, action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pdf", type=Path, default=config.DEFAULT_PDF)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", choices=["marker", "docling"])
    args = ap.parse_args()

    _seed_everything()
    triage = load_triage()

    if args.all:
        numbers = [c["number"] for c in triage["boundary_map"]["chapters"]]
    elif args.chapter:
        numbers = args.chapter
    else:
        raise SystemExit("specify --chapter N (repeatable) or --all")

    needed = [
        n
        for n in numbers
        if args.force
        or not (MARKER_DIR / f"{config.chapter_id(n)}.json").exists()
        or not (DOCLING_DIR / f"{config.chapter_id(n)}.json").exists()
    ]

    marker = docling = None
    if needed:
        if args.only != "docling":
            print("loading marker models ...", flush=True)
            marker = MarkerRunner()
        if args.only != "marker":
            print("loading docling models ...", flush=True)
            docling = DoclingRunner()

    for number in numbers:
        first, last = chapter_range(triage, number)
        summary = extract_chapter(
            number, args.pdf, first, last, marker, docling, args.force
        )
        print(
            f"ch{number:02d} pp{first}-{last}: "
            f"marker={summary.get('marker', 'skipped')} "
            f"docling={summary.get('docling', 'skipped')}",
            flush=True,
        )


if __name__ == "__main__":
    main()
