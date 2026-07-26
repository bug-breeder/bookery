"""Shared paths, constants, and version capture for the pipeline.

Every stage imports its paths from here so that a stage never has to guess
where another stage put its artifacts.
"""

from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PDF_DIR = ROOT / "pdf"
WORK = ROOT / "work"
EXTRACT_DIR = WORK / "extract"
RECONCILE_DIR = WORK / "reconcile"
PROOF_DIR = WORK / "proofs"
TMP_DIR = WORK / "tmp"

SITE = ROOT / "site"
DOCS = SITE / "docs"
STATIC = SITE / "static"
STATIC_IMG = STATIC / "img"

REPORTS = ROOT / "reports"
OVERRIDES = ROOT / "overrides"

DEFAULT_PDF = PDF_DIR / "networks-book.pdf"
TRIAGE_JSON = WORK / "triage.json"
ASSETS_JSON = WORK / "assets.json"
MANIFEST_JSON = ROOT / "MANIFEST.json"

# Figure crops are rendered at print resolution; page proofs used for visual
# adjudication only need to be legible.
FIGURE_DPI = 300
PROOF_DPI = 200

# Determinism: every model-backed stage is pinned to this seed.
SEED = 20100610

ALL_DIRS = [
    PDF_DIR,
    WORK,
    EXTRACT_DIR,
    RECONCILE_DIR,
    PROOF_DIR,
    TMP_DIR,
    REPORTS,
    OVERRIDES,
]


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def chapter_id(n: int) -> str:
    """Canonical chapter identifier, e.g. 2 -> 'ch02'."""
    return f"ch{n:02d}"


_ROMAN_LOWER = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]


def printed_page_range(chapter: dict) -> str:
    """Human-readable printed page range for a boundary-map chapter entry.

    The Preface (chapter 0) is paginated in lowercase roman numerals, a
    distinct sequence from the book's arabic body pagination, so showing its
    `printed_page` as an arabic number would look like it overlaps Chapter
    1's own pp. 1-20.
    """
    span = chapter["pdf_page_end"] - chapter["pdf_page"]
    start = chapter["printed_page"]
    if chapter["number"] == 0:
        romans = [
            _ROMAN_LOWER[i - 1] if i <= len(_ROMAN_LOWER) else str(i)
            for i in range(start, start + span + 1)
        ]
        return f"{romans[0]}-{romans[-1]}" if len(romans) > 1 else romans[0]
    return f"{start}-{start + span}"


def _cmd_version(args: list[str]) -> str | None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or "") + (out.stderr or "")
    return text.strip().splitlines()[0] if text.strip() else None


def _pkg_version(name: str) -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(name)
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def tool_versions() -> dict[str, str | None]:
    """Versions recorded into the manifest so a run can be reproduced."""
    import platform

    versions: dict[str, str | None] = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()}-{platform.machine()}",
        "marker-pdf": _pkg_version("marker-pdf"),
        "docling": _pkg_version("docling"),
        "docling-core": _pkg_version("docling-core"),
        "surya-ocr": _pkg_version("surya-ocr"),
        "torch": _pkg_version("torch"),
        "transformers": _pkg_version("transformers"),
        "pymupdf": _pkg_version("pymupdf"),
        "pdftotext": _cmd_version(["pdftotext", "-v"]),
    }

    try:
        import torch

        if torch.cuda.is_available():
            versions["accelerator"] = "cuda"
        elif torch.backends.mps.is_available():
            versions["accelerator"] = "mps"
        else:
            versions["accelerator"] = "cpu"
    except Exception:
        versions["accelerator"] = "unknown"

    return versions
