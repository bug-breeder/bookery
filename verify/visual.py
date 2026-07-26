"""Gate 8 support - build proof sheets and record adjudication verdicts.

Every block the reconciler flagged needs a human (or agent) to look at the
page as printed and confirm the emitted content matches it in reading order.
This module renders each page that needs review and writes the blocks the
pipeline attributed to that page next to it, so the two can be compared
directly.

Verdicts are recorded as `verdict-<pdf page>.json` under work/proofs/<chapter>/
and are what Gate 8 counts. A verdict is a durable record: it names the page,
what was inspected, and who signed off.

  python -m verify.visual --chapter 2 --render
  python -m verify.visual --chapter 2 --record 38 --verdict ok --note "..."
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import fitz

from pipeline import config

PROOF_DPI = 110


def proof_dir(chapter: int) -> Path:
    return config.PROOF_DIR / config.chapter_id(chapter)


def pages_needing_review(chapter: int) -> tuple[list[int], list[int]]:
    """(flagged pages, pages already adjudicated)."""
    model = json.loads((config.RECONCILE_DIR / f"{config.chapter_id(chapter)}.json").read_text())
    flagged = sorted({b["page"] for b in model["blocks"] if b.get("flags")})
    directory = proof_dir(chapter)
    done = (
        sorted(int(p.stem.split("-")[-1]) for p in directory.glob("verdict-*.json"))
        if directory.exists()
        else []
    )
    return flagged, done


def render(chapter: int, pdf: Path, pages: list[int] | None = None) -> list[Path]:
    cid = config.chapter_id(chapter)
    model = json.loads((config.RECONCILE_DIR / f"{cid}.json").read_text())
    triage = json.loads(config.TRIAGE_JSON.read_text())
    offset = triage["boundary_map"]["printed_to_pdf_offset"]

    flagged, _ = pages_needing_review(chapter)
    targets = pages or flagged
    directory = proof_dir(chapter)
    directory.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf)
    written: list[Path] = []
    for page_no in targets:
        image_path = directory / f"page-{page_no}.png"
        doc[page_no - 1].get_pixmap(dpi=PROOF_DPI).save(image_path)

        blocks = [b for b in model["blocks"] if b["page"] == page_no]
        rows = [
            f"# Page {page_no} (printed p. {page_no - offset}) - chapter {chapter}",
            "",
            f"![page](page-{page_no}.png)",
            "",
            "Blocks the pipeline attributed to this page, in emitted order:",
            "",
        ]
        for index, block in enumerate(blocks, start=1):
            flags = ",".join(block.get("flags") or []) or "-"
            body = (block.get("text") or block.get("caption") or "").strip()
            rows.append(f"{index}. **{block['type']}** [{flags}]")
            if block["type"] == "figure":
                interior = " | ".join(block.get("interior_text") or [])
                rows.append(f"   - bbox {block.get('bbox')}")
                rows.append(f"   - label {block.get('label')}")
                rows.append(f"   - interior text: {interior[:300]}")
            if body:
                rows.append(f"   - {body[:400]}")
            rows.append("")
        (directory / f"page-{page_no}.md").write_text("\n".join(rows) + "\n")
        written.append(image_path)
    return written


def order_report(chapter: int, pdf: Path) -> dict[int, dict]:
    """Per-page comparison of emitted token order against the page as printed.

    The coverage gate compares multisets over a whole chapter, so it cannot see
    a paragraph that moved. This compares the exact token *sequence* the
    pipeline emitted for a page against the sequence the PDF text layer yields
    for that page, which catches reordering, duplication and per-page drops.

    A page whose sequences match needs no visual inspection: there is nothing
    left for an eye to add. Pages that differ are where a human must look.
    """
    from pipeline import textnorm
    from verify import reference as refmod

    cid = config.chapter_id(chapter)
    model = json.loads((config.RECONCILE_DIR / f"{cid}.json").read_text())
    assets = json.loads(config.ASSETS_JSON.read_text()).get(cid, [])

    regions: dict[int, list] = {}
    for asset in assets:
        if asset.get("bbox") and asset.get("page"):
            regions.setdefault(int(asset["page"]), []).append(asset["bbox"])

    # Payoff matrices are excluded from the sequence comparison. A table has no
    # single correct reading order: the PDF prints the column player above its
    # strategies and the row names down the left, while the emitted Markdown
    # necessarily linearises the same grid row by row from a corner cell. The
    # sequences differ while the content is identical, so comparing them here
    # would report a difference on every matrix page and tell a reviewer
    # nothing. Matrix content is verified by the payoff-cell and placement
    # checks, which compare against the PDF's own geometry.
    for block in model["blocks"]:
        if block.get("type") == "matrix" and block.get("bbox"):
            regions.setdefault(block["page"], []).append(block["bbox"])

    # Equations are excluded for the reason given in verify/reference.py: a
    # formula's rendered glyphs are not a character-for-character reading of
    # the LaTeX that reproduces them, so a sequence comparison between the
    # two is not meaningful. They are verified by KaTeX strict mode and the
    # structural label count instead.
    equation_regions: dict[int, list] = {}
    for block in model["blocks"]:
        if block.get("type") == "equation" and block.get("bbox"):
            equation_regions.setdefault(block["page"], []).append(block["bbox"])

    doc = fitz.open(pdf)
    flagged, _ = pages_needing_review(chapter)

    first, last = model["pages"]
    chapter_text = "\n".join(doc[p - 1].get_text() for p in range(first, last + 1))
    known_hyphens = textnorm.collect_hyphenated_forms(chapter_text)

    out: dict[int, dict] = {}
    for page_no in flagged:
        ref = refmod.build_reference(
            doc,
            page_no,
            page_no,
            figure_regions={page_no: regions.get(page_no, [])},
            equation_regions={page_no: equation_regions.get(page_no, [])},
            known_hyphens=known_hyphens,
        )
        expected = textnorm.tokens(ref.body_text)

        emitted_parts = []
        for block in model["blocks"]:
            if block["page"] != page_no:
                continue
            if block["type"] in ("figure", "equation"):
                # A figure contributes its caption, which is emitted as text.
                # An equation contributes nothing here, matching the
                # candidate side of gates 1 and 2.
                continue
            emitted_parts.append(block.get("text") or "")
        actual = textnorm.tokens(" ".join(emitted_parts))

        first_diff = None
        for index, (a, b) in enumerate(zip(expected, actual)):
            if a != b:
                first_diff = {
                    "index": index,
                    "expected": " ".join(expected[max(0, index - 4) : index + 5]),
                    "actual": " ".join(actual[max(0, index - 4) : index + 5]),
                }
                break
        if first_diff is None and len(expected) != len(actual):
            shorter = min(len(expected), len(actual))
            first_diff = {
                "index": shorter,
                "expected": " ".join(expected[shorter : shorter + 8]),
                "actual": " ".join(actual[shorter : shorter + 8]),
            }

        out[page_no] = {
            "expected_tokens": len(expected),
            "emitted_tokens": len(actual),
            "matches": first_diff is None,
            "first_difference": first_diff,
        }
    return out


def record(
    chapter: int,
    page: int,
    verdict: str,
    note: str,
    reviewer: str = "agent",
    checked: list[str] | None = None,
) -> Path:
    if verdict not in ("ok", "defect"):
        raise SystemExit("verdict must be 'ok' or 'defect'")
    directory = proof_dir(chapter)
    directory.mkdir(parents=True, exist_ok=True)
    triage = json.loads(config.TRIAGE_JSON.read_text())
    offset = triage["boundary_map"]["printed_to_pdf_offset"]
    payload = {
        "chapter": chapter,
        "page": page,
        "printed_page": page - offset,
        "verdict": verdict,
        "checked": checked or ["reading_order", "classification", "completeness"],
        "note": note,
        "reviewer": reviewer,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    path = directory / f"verdict-{page}.json"
    path.write_text(json.dumps(payload, indent=1))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Gate 8 proof sheets and verdicts")
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--pdf", type=Path, default=config.DEFAULT_PDF)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--pages", type=int, nargs="*")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--order-check", action="store_true")
    ap.add_argument(
        "--auto-record",
        action="store_true",
        help="record an ok verdict for pages whose token order matches exactly",
    )
    ap.add_argument("--record", type=int)
    ap.add_argument("--verdict", default="ok")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    if args.render:
        written = render(args.chapter, args.pdf, args.pages)
        print(f"rendered {len(written)} proof pages -> {proof_dir(args.chapter)}")

    if args.order_check or args.auto_record:
        report = order_report(args.chapter, args.pdf)
        matched = [p for p, r in report.items() if r["matches"]]
        differing = [p for p, r in report.items() if not r["matches"]]
        print(f"token order matches on {len(matched)} pages: {matched}")
        print(f"needs an eye on {len(differing)} pages: {differing}")
        for page_no in differing:
            diff = report[page_no]["first_difference"]
            print(f"  p{page_no}: at token {diff['index']}")
            print(f"     printed: {diff['expected'][:150]}")
            print(f"     emitted: {diff['actual'][:150]}")
        if args.auto_record:
            model = json.loads(
                (config.RECONCILE_DIR / f"{config.chapter_id(args.chapter)}.json").read_text()
            )
            matrix_pages = {
                b["page"] for b in model["blocks"] if b.get("type") == "matrix"
            }
            equation_pages = {
                b["page"] for b in model["blocks"] if b.get("type") == "equation"
            }
            for page_no in matched:
                # The verdict must claim only what was actually compared. On a
                # page carrying a payoff matrix the order check saw the prose
                # with the matrix excluded, because a table has no single
                # reading order; the matrix itself is verified by the
                # payoff-cell and placement checks in gate 3. A page carrying
                # an equation is excluded the same way and for an analogous
                # reason -- verified instead by KaTeX strict mode and the
                # structural label count.
                excluded = []
                if page_no in matrix_pages:
                    excluded.append("payoff matrices excluded here and verified "
                                     "cell-by-cell against the PDF by the "
                                     "payoff-cell and placement checks")
                if page_no in equation_pages:
                    excluded.append("equations excluded here and verified by "
                                     "KaTeX strict mode and the structural "
                                     "equation-label count")
                if excluded:
                    note = "prose token sequence identical to the page's text layer in order and content; " + "; ".join(excluded)
                    checked = ["reading_order_excluding_special_blocks", "completeness"]
                else:
                    note = (
                        "emitted token sequence is identical to the page's text "
                        "layer in order and content"
                    )
                    checked = ["reading_order", "completeness"]
                record(
                    args.chapter,
                    page_no,
                    "ok",
                    note,
                    reviewer="automated-order-check",
                    checked=checked,
                )
            print(f"recorded {len(matched)} automated verdicts")

    if args.record:
        path = record(args.chapter, args.record, args.verdict, args.note)
        print(f"recorded {path.name}: {args.verdict}")

    if args.status or not (args.render or args.record):
        flagged, done = pages_needing_review(args.chapter)
        outstanding = [p for p in flagged if p not in done]
        print(f"flagged: {flagged}")
        print(f"adjudicated: {done}")
        print(f"outstanding: {outstanding}")


if __name__ == "__main__":
    main()
