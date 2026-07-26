"""Runs the gates for a chapter and writes the report.

Usable standalone against any chapter at any point in the pipeline's
progress. Stages that have not run yet produce BLOCKED gates rather than
absent ones, so a report always accounts for all eight.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from pipeline import config
from verify import candidate, gates, reference, site_index

KATEX_SCRIPT = config.ROOT / "verify" / "katex_check.mjs"


def load_triage() -> dict:
    if not config.TRIAGE_JSON.exists():
        raise SystemExit("FATAL: work/triage.json missing. Run stage0 first.")
    return json.loads(config.TRIAGE_JSON.read_text())


def chapter_meta(triage: dict, number: int) -> dict:
    for ch in triage["boundary_map"]["chapters"]:
        if ch["number"] == number:
            return ch
    raise SystemExit(f"FATAL: chapter {number} not in boundary map")


def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _find_emitted(triage: dict, meta: dict) -> Path | None:
    """Locate the emitted chapter file, whatever part directory it landed in."""
    if not config.DOCS.exists():
        return None
    matches = sorted(config.DOCS.rglob(f"{meta['number']:02d}-*.md"))
    return matches[0] if matches else None


@dataclass
class ChapterReport:
    chapter: int
    pages: str
    status: str
    gates: dict
    review_queue: list
    versions: dict

    def as_dict(self) -> dict:
        return {
            "chapter": self.chapter,
            "pages": self.pages,
            "status": self.status,
            "gates": self.gates,
            "review_queue": self.review_queue,
            "versions": self.versions,
        }


def verify_chapter(number: int, run_build: bool = False) -> ChapterReport:
    triage = load_triage()
    meta = chapter_meta(triage, number)
    first, last = meta["pdf_page"], meta["pdf_page_end"]
    offset = triage["boundary_map"]["printed_to_pdf_offset"]

    doc = fitz.open(config.ROOT / triage["source"]["path"])

    model = _read_json(config.RECONCILE_DIR / f"{config.chapter_id(number)}.json")
    assets_all = _read_json(config.ASSETS_JSON) or {}
    chapter_assets = (assets_all.get(config.chapter_id(number)) or []) if isinstance(assets_all, dict) else []

    figure_regions: dict[int, list] = {}
    for asset in chapter_assets:
        if asset.get("bbox") and asset.get("page"):
            figure_regions.setdefault(int(asset["page"]), []).append(asset["bbox"])

    equation_regions: dict[int, list] = {}
    for block in (model or {}).get("blocks", []):
        if block.get("type") == "equation" and block.get("bbox") and block.get("page"):
            equation_regions.setdefault(int(block["page"]), []).append(block["bbox"])

    ref = reference.build_reference(
        doc,
        first,
        last,
        figure_regions=figure_regions or None,
        equation_regions=equation_regions or None,
    )
    counts = reference.count_structures(doc, first, last)

    emitted = _find_emitted(triage, meta)
    review: list[dict] = []
    results: list[gates.Gate] = []

    # ---- Gates 1 and 2 need the emitted document.
    if emitted is None:
        results.append(gates._blocked("text_coverage", "no emitted .md for this chapter"))
        results.append(gates._blocked("numeric_fidelity", "no emitted .md for this chapter"))
    else:
        cand_text = candidate.markdown_to_text(emitted.read_text())
        results.append(gates.gate_text_coverage(ref.body_text, cand_text))
        results.append(gates.gate_numeric_fidelity(ref.body_text, cand_text))

    # ---- Gate 3 compares the canonical model against the PDF's own counts.
    if model is None:
        results.append(gates._blocked("structural_counts", "no reconcile/ model for this chapter"))
    else:
        expected = counts.as_dict()
        got = model.get("counts") or _counts_from_model(model)
        structural = gates.gate_structural_counts(expected, got)

        # Payoff matrices are structure the extractors do not report at all, so
        # they get their own check against the PDF's own cells.
        payoff = gates.check_payoff_cells(
            _pdf_payoff_cells(doc, first, last),
            emitted.read_text() if emitted else "",
        )
        structural.detail["payoff_cells"] = payoff

        placement = gates.check_payoff_placement(
            [b for b in model.get("blocks", []) if b.get("type") == "matrix"], doc
        )
        structural.detail["payoff_placement"] = placement

        if not payoff["ok"] or not placement["ok"]:
            structural.status = gates.FAIL
        results.append(structural)

    # ---- Gate 4. A chapter stage3 has processed has a key in assets.json
    # even when it found zero figures (the Preface, for instance); only the
    # key's absence means the stage has not run yet.
    if config.chapter_id(number) not in assets_all:
        results.append(gates._blocked("assets", "stage3_assets has not run for this chapter"))
    else:
        # A figure the book labels "Figure N.N" is not required to be an image.
        # The payoff matrices are emitted as Markdown tables and deliberately
        # have no asset, so demanding a PNG for them would fail the gate on 25
        # of chapter 6's 28 figures. They are verified as tables instead, by the
        # payoff-cell check below.
        as_tables = {
            b.get("label")
            for b in (model or {}).get("blocks", [])
            if b.get("type") == "matrix" and b.get("label")
        }
        referenced = set(counts.figures) - as_tables
        asset_gate = gates.gate_assets(chapter_assets, referenced, config.SITE / "static")
        asset_gate.detail["emitted_as_tables"] = sorted(as_tables, key=_label_key)

        # Existing on disk is not the same as reaching the reader.
        built = _built_html_for(triage, meta)
        render = gates.gate_assets_render(
            chapter_assets,
            emitted.read_text() if emitted else "",
            built,
        )
        asset_gate.detail["render"] = render
        if not render["ok"]:
            asset_gate.status = gates.FAIL
        results.append(asset_gate)

    # ---- Gate 5.
    equations = [b for b in (model or {}).get("blocks", []) if b.get("type") == "equation"]
    if model is None:
        results.append(gates._blocked("math", "no reconcile/ model for this chapter"))
    else:
        results.append(gates.gate_math(equations, KATEX_SCRIPT))

    # ---- Gate 6 resolves against the emitted site, not the pipeline's intent.
    if emitted is None:
        results.append(gates._blocked("xrefs", "no emitted .md for this chapter"))
    else:
        index = site_index.build(config.DOCS)
        links = site_index.links_in(emitted.read_text())
        xref_report = _read_json(config.WORK / f"xrefs-{config.chapter_id(number)}.json") or {}
        deferred = xref_report.get("unresolved") or []
        in_scope = _emitted_chapters(triage)
        results.append(gates.gate_xrefs(links, index, deferred, in_scope))

    # ---- Gate 7.
    results.append(gates.gate_build(config.SITE, run_build=run_build))

    # ---- Gate 8.
    flagged = sorted({b["page"] for b in (model or {}).get("blocks", []) if b.get("flags")})
    proofs = config.PROOF_DIR / config.chapter_id(number)
    checked = (
        sorted(int(p.stem.split("-")[-1]) for p in proofs.glob("verdict-*.json"))
        if proofs.exists()
        else []
    )
    sampled = list(range(first, last + 1))[:: max(1, (last - first + 1) // 2 or 1)]
    if model is None:
        results.append(gates._blocked("visual", "no reconcile/ model for this chapter", hard=False))
    else:
        results.append(gates.gate_visual(checked, flagged, sampled))

    # ---- Anything the reference builder removed for a reason that is not
    # ---- plain page furniture is an unexplained drop and must be reviewed.
    for removed in ref.removed:
        if removed.reason in ("integer_soup",):
            review.append(
                {
                    "page": removed.page,
                    "printed_page": removed.page - offset,
                    "kind": removed.reason,
                    "text": removed.text[:160],
                }
            )

    for gate in results:
        if gate.status == gates.FAIL:
            review.append({"kind": "gate_failure", "gate": gate.name, "detail": gate.detail})
        elif gate.status == gates.BLOCKED:
            review.append(
                {"kind": "gate_blocked", "gate": gate.name, "reason": gate.detail.get("reason")}
            )

    hard = [g for g in results if g.hard]
    if any(g.status == gates.FAIL for g in hard):
        status = "FAIL"
    elif any(g.status == gates.BLOCKED for g in hard):
        status = "BLOCKED"
    elif review:
        status = "REVIEW"
    else:
        status = "PASS"

    return ChapterReport(
        chapter=number,
        pages=config.printed_page_range(meta),
        status=status,
        gates={g.name: g.as_dict() for g in results},
        review_queue=review,
        versions=config.tool_versions(),
    )


def _pdf_payoff_cells(doc: fitz.Document, first: int, last: int) -> list[str]:
    """Every payoff-shaped line the PDF prints in the chapter's math face.

    Read directly from the text layer with no grid logic, so it is an
    independent account of what the tables should contain.
    """
    from pipeline import matrix as matrixlib, pdfutil

    cells: list[str] = []
    for page_no in range(first, last + 1):
        for line in pdfutil.page_lines(doc, page_no, rows=False):
            text = line.text.strip()
            if matrixlib.is_payoff(text) and any(
                hint in font for font in line.fonts for hint in matrixlib.MATH_FONT_HINTS
            ):
                cells.append(text)
    return cells


def _label_key(label: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(label).split("."))
    except ValueError:
        return (0,)


def _built_html_for(triage: dict, meta: dict) -> str | None:
    """Built page for a chapter, when the site has been built."""
    build = config.SITE / "build"
    if not build.exists():
        return None
    matches = sorted(build.rglob(f"{meta['number']:02d}-*/index.html"))
    return matches[0].read_text() if matches else None


def _emitted_chapters(triage: dict) -> set[int]:
    """Chapters that currently have an emitted document."""
    if not config.DOCS.exists():
        return set()
    found: set[int] = set()
    for path in config.DOCS.rglob("*.md"):
        match = re.match(r"^(\d+)-", path.stem)
        if match:
            found.add(int(match.group(1)))
    return found


def _counts_from_model(model: dict) -> dict:
    blocks = model.get("blocks", [])
    return {
        "figures": [
            b["label"]
            for b in blocks
            if b.get("type") in ("figure", "matrix") and b.get("label")
        ],
        "tables": [b["label"] for b in blocks if b.get("type") == "table" and b.get("label")],
        "sections": [b["label"] for b in blocks if b.get("type") == "heading" and b.get("label")],
        "equations": [b["label"] for b in blocks if b.get("type") == "equation" and b.get("label")],
        "exercises": sum(1 for b in blocks if b.get("type") == "exercise"),
        "footnotes": sum(1 for b in blocks if b.get("type") == "footnote"),
    }


def write_report(report: ChapterReport) -> Path:
    config.REPORTS.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS / f"{config.chapter_id(report.chapter)}.json"
    path.write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    return path


def write_review_queue() -> Path:
    """Aggregate every chapter report into one review queue."""
    rows: list[str] = [
        "# Review queue",
        "",
        "Generated by `python -m verify.runner`. Every entry is an item that",
        "was not verified automatically. A chapter is not shippable while it",
        "has entries here.",
        "",
    ]
    reports = sorted(config.REPORTS.glob("ch*.json"))
    if not reports:
        rows.append("_No chapter reports yet._")
    for path in reports:
        data = json.loads(path.read_text())
        rows.append(f"## Chapter {data['chapter']} - {data['status']} (pp. {data['pages']})")
        rows.append("")
        queue = data.get("review_queue") or []
        if not queue:
            rows.append("Nothing outstanding.")
            rows.append("")
            continue
        rows.append("| kind | where | detail |")
        rows.append("| --- | --- | --- |")
        for item in queue:
            kind = item.get("kind", "?")
            where = item.get("gate") or f"p{item.get('printed_page', item.get('page', '?'))}"
            detail = item.get("reason") or item.get("text") or ""
            if not detail and item.get("detail"):
                detail = json.dumps(item["detail"])[:180]
            detail = str(detail).replace("|", "\\|")[:180]
            rows.append(f"| {kind} | {where} | {detail} |")
        rows.append("")

    path = config.REPORTS / "review-queue.md"
    path.write_text("\n".join(rows) + "\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the verification gates for a chapter")
    ap.add_argument("--chapter", type=int, action="append", required=True)
    ap.add_argument("--build", action="store_true", help="also run the site build gate")
    args = ap.parse_args()

    for number in args.chapter:
        report = verify_chapter(number, run_build=args.build)
        path = write_report(report)
        print(f"chapter {number}: {report.status}  -> {path.relative_to(config.ROOT)}")
        for name, result in report.gates.items():
            print(f"    {name:<18} {result['status']}")

    queue = write_review_queue()
    print(f"review queue: {queue.relative_to(config.ROOT)}")

    # Regenerated on every run so PROGRESS.md cannot drift from the reports.
    from pipeline import status

    status.write()


if __name__ == "__main__":
    main()
