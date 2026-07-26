"""The eight verification gates.

Every gate returns a Gate result rather than raising, so one chapter run
produces a complete picture instead of stopping at the first problem.

Status vocabulary, and the distinction matters:

  PASS     the gate ran and the chapter satisfied it
  FAIL     the gate ran and the chapter did not satisfy it
  BLOCKED  the gate could not run because an input artifact is missing

BLOCKED is never treated as success. A chapter is only PASS when every hard
gate is PASS and the review queue is empty, which is what stops a partially
processed chapter from being reported as shipped.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import config, textnorm

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
REVIEWED = "REVIEWED"

TEXT_COVERAGE_THRESHOLD = 0.999
MIN_ASSET_PIXELS = 100
MIN_EQUATION_CHARS = 3


@dataclass
class Gate:
    name: str
    status: str
    detail: dict = field(default_factory=dict)
    hard: bool = True

    @property
    def ok(self) -> bool:
        return self.status in (PASS, REVIEWED)

    def as_dict(self) -> dict:
        return {"status": self.status, **self.detail}


def _blocked(name: str, why: str, hard: bool = True) -> Gate:
    return Gate(name, BLOCKED, {"reason": why}, hard=hard)


# --------------------------------------------------------------------------
# Gate 1 - text coverage
# --------------------------------------------------------------------------


def gate_text_coverage(reference_text: str, candidate_text: str, page_of_token=None) -> Gate:
    """Token multiset recall of the reference against the emitted document."""
    ref = collections.Counter(textnorm.tokens(reference_text))
    cand = collections.Counter(textnorm.tokens(candidate_text))

    total = sum(ref.values())
    if total == 0:
        return _blocked("text_coverage", "reference text is empty")

    missing = ref - cand
    recovered = total - sum(missing.values())
    value = recovered / total

    detail = {
        "value": round(value, 6),
        "threshold": TEXT_COVERAGE_THRESHOLD,
        "reference_tokens": total,
        "candidate_tokens": sum(cand.values()),
        "missing_count": sum(missing.values()),
        "missing_sample": [
            {"token": tok, "n": n, "page": (page_of_token or {}).get(tok)}
            for tok, n in missing.most_common(40)
        ],
    }
    status = PASS if value >= TEXT_COVERAGE_THRESHOLD else FAIL
    return Gate("text_coverage", status, detail)


# --------------------------------------------------------------------------
# Gate 2 - numeric fidelity
# --------------------------------------------------------------------------


def gate_numeric_fidelity(reference_text: str, candidate_text: str) -> Gate:
    """Exact multiset equality of numeric literals. No tolerance."""
    ref = collections.Counter(textnorm.numbers(reference_text))
    cand = collections.Counter(textnorm.numbers(candidate_text))

    missing = ref - cand
    extra = cand - ref

    detail = {
        "reference_numbers": sum(ref.values()),
        "candidate_numbers": sum(cand.values()),
        "missing": [{"value": v, "n": n} for v, n in missing.most_common(60)],
        "extra": [{"value": v, "n": n} for v, n in extra.most_common(60)],
    }
    status = PASS if not missing and not extra else FAIL
    return Gate("numeric_fidelity", status, detail)


# --------------------------------------------------------------------------
# Gate 3 - structural counts
# --------------------------------------------------------------------------


def gate_structural_counts(expected: dict, got: dict) -> Gate:
    """Counts from the canonical model against counts taken from the PDF."""
    detail: dict = {}
    ok = True

    for key in ("figures", "tables", "sections", "equations"):
        exp = list(expected.get(key) or [])
        act = list(got.get(key) or [])
        missing = sorted(set(exp) - set(act))
        surplus = sorted(set(act) - set(exp))
        detail[key] = {
            "expected": len(exp),
            "got": len(act),
            "missing_labels": missing,
            "unexpected_labels": surplus,
        }
        if missing or surplus:
            ok = False

    for key in ("exercises", "footnotes"):
        exp = expected.get(key)
        act = got.get(key)
        if exp is None and act is None:
            continue
        detail[key] = {"expected": exp, "got": act}
        if exp != act:
            ok = False

    return Gate("structural_counts", PASS if ok else FAIL, detail)


# --------------------------------------------------------------------------
# Gate 4 - asset integrity
# --------------------------------------------------------------------------


def gate_assets(assets: list[dict], referenced_labels: set[str], root: Path) -> Gate:
    """Files exist, are big enough, are described, and are not duplicates.

    An empty ``assets`` list is not itself a problem: a chapter (the Preface,
    say) can genuinely contain zero figures, and the loop below already
    reports every *referenced* label with no matching asset as
    ``referenced_but_absent``. Blocking here unconditionally would make a
    figure-free chapter unable to ever pass this gate, the same trap gate 5
    avoids for a chapter with zero equations. Whether stage3 has run at all
    for this chapter is the caller's distinction to make (it has the
    assets.json key to check), not this gate's.
    """
    problems: list[dict] = []
    hashes: dict[str, str] = {}

    for asset in assets:
        label = asset.get("label", "?")
        rel = asset.get("file")
        path = root / rel if rel else None

        if not rel or path is None or not path.exists():
            problems.append({"label": label, "issue": "file_missing", "file": rel})
            continue
        if path.stat().st_size == 0:
            problems.append({"label": label, "issue": "zero_bytes", "file": rel})
            continue

        width, height = asset.get("width", 0), asset.get("height", 0)
        if width < MIN_ASSET_PIXELS or height < MIN_ASSET_PIXELS:
            problems.append(
                {"label": label, "issue": "too_small", "width": width, "height": height}
            )
        if not (asset.get("alt") or "").strip():
            problems.append({"label": label, "issue": "empty_alt"})

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in hashes:
            # Identical bytes for two figures means the crop window did not
            # move, which is a silent and very plausible cropping bug.
            problems.append(
                {"label": label, "issue": "duplicate_image", "same_as": hashes[digest]}
            )
        else:
            hashes[digest] = label

    have = {a.get("label") for a in assets}
    for label in sorted(referenced_labels - have):
        problems.append({"label": label, "issue": "referenced_but_absent"})

    detail = {"assets": len(assets), "problems": problems}
    return Gate("assets", PASS if not problems else FAIL, detail)


# --------------------------------------------------------------------------
# Gate 5 - math renders
# --------------------------------------------------------------------------


def gate_math(equations: list[dict], katex_script: Path) -> Gate:
    """Every equation must parse under KaTeX in strict mode."""
    if not equations:
        return Gate("math", PASS, {"equations": 0, "errors": [], "note": "no equations"})
    if not katex_script.exists():
        return _blocked("math", f"KaTeX checker not built at {katex_script}")

    payload = json.dumps(
        [{"id": e.get("id"), "page": e.get("page"), "latex": e.get("latex", "")} for e in equations]
    )
    try:
        proc = subprocess.run(
            ["node", str(katex_script)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _blocked("math", f"could not run node: {exc}")

    if proc.returncode not in (0, 1):
        return _blocked("math", f"katex checker crashed: {proc.stderr[:300]}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _blocked("math", f"katex checker returned non-JSON: {proc.stdout[:200]}")

    errors = result.get("errors", [])
    suspicious = [
        {"id": e.get("id"), "page": e.get("page"), "latex": e.get("latex")}
        for e in equations
        if len((e.get("latex") or "").strip()) < MIN_EQUATION_CHARS
    ]

    detail = {
        "equations": len(equations),
        "errors": errors,
        "suspiciously_short": suspicious,
    }
    status = PASS if not errors and not suspicious else FAIL
    return Gate("math", status, detail)


# --------------------------------------------------------------------------
# Gate 6 - cross-reference resolution
# --------------------------------------------------------------------------


def gate_assets_render(
    assets: list[dict], emitted_markdown: str, built_html: str | None
) -> dict:
    """Every figure must actually appear as an image, not just exist on disk.

    Alt text taken from a caption can contain a bracket, which terminates the
    Markdown image syntax early and makes the figure render as literal prose.
    Checking only that the PNG exists misses this entirely, so the emitted
    Markdown and, when a build is present, the built HTML are both checked.
    """
    expected = [a for a in assets if a.get("file") and not a.get("issue")]
    missing_in_markdown = [
        a["label"] for a in expected if f"]({'/' + a['file']})" not in emitted_markdown
    ]

    missing_in_html: list[str] = []
    if built_html is not None:
        for asset in expected:
            stem = Path(asset["file"]).stem
            if not re.search(rf"<img[^>]*{re.escape(stem)}[-.]", built_html):
                missing_in_html.append(asset["label"])

    return {
        "figures": len(expected),
        "missing_in_markdown": missing_in_markdown,
        "missing_in_html": missing_in_html,
        "html_checked": built_html is not None,
        "ok": not missing_in_markdown and not missing_in_html,
    }


def check_payoff_cells(pdf_cells: list[str], emitted_markdown: str) -> dict:
    """Every payoff the PDF prints must appear in an emitted table, and vice versa.

    Deliberately independent of the grid-assembly logic that produced the
    tables. The reference side is every payoff-shaped line in the page's text
    layer, read straight from the PDF with no notion of rows or columns; the
    candidate side is the payoff-shaped cells of the emitted Markdown tables.
    Comparing the two as multisets catches a dropped, duplicated or corrupted
    cell, which is what would otherwise pass silently: a matrix that renders
    beautifully with one wrong number looks exactly like a correct one.

    It cannot catch a cell placed in the wrong column, since both sides are
    order-free. That is what the per-page order check and visual adjudication
    are for.
    """
    from pipeline import matrix as matrixlib

    emitted: list[str] = []
    for line in emitted_markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= set("| -"):
            continue
        for cell in stripped.strip("|").split("|"):
            cell = cell.replace("**", "").strip()
            if matrixlib.is_payoff(cell):
                emitted.append(_canonical_payoff(cell))

    reference = [_canonical_payoff(c) for c in pdf_cells]
    ref = collections.Counter(reference)
    cand = collections.Counter(emitted)
    missing, extra = ref - cand, cand - ref

    return {
        "pdf_cells": sum(ref.values()),
        "emitted_cells": sum(cand.values()),
        "missing": [{"cell": c, "n": n} for c, n in missing.most_common(40)],
        "extra": [{"cell": c, "n": n} for c, n in extra.most_common(40)],
        "ok": not missing and not extra,
    }


def check_payoff_placement(matrices: list[dict], doc) -> dict:
    """Confirm each cell sits where the model says it does, against the PDF.

    The multiset check cannot see a cell put in the wrong column, because it
    compares without order. This re-reads the page geometry and requires, for
    every cell, that the PDF prints that value on the row its row-header
    occupies and in the column its column-header is centred on. A transposed
    matrix, a shifted row or a cell copied into the wrong column all fail here,
    and none of them would fail anything else.
    """
    from pipeline import matrix as matrixlib, pdfutil, textnorm

    violations: list[dict] = []
    checked = 0

    for block in matrices:
        cells = block.get("cells") or []
        bbox = block.get("bbox")
        if len(cells) < 2 or not bbox:
            continue

        lines = [
            line
            for line in pdfutil.page_lines(doc, block["page"], rows=False)
            if line.bbox[0] >= bbox[0] - 4 and line.bbox[2] <= bbox[2] + 4
            and line.bbox[1] >= bbox[1] - 4 and line.bbox[3] <= bbox[3] + 4
        ]

        def find(text: str):
            wanted = textnorm.for_output(text).strip()
            return [
                line
                for line in lines
                if textnorm.for_output(line.text).strip() == wanted
            ]

        col_centres: dict[int, list[float]] = {}
        for index, header in enumerate(cells[0]):
            if index == 0 or not header:
                continue
            col_centres[index] = [
                (line.bbox[0] + line.bbox[2]) / 2.0 for line in find(header)
            ]

        for r, row in enumerate(cells[1:], start=1):
            row_header = row[0] if row else ""
            row_ys = [
                (line.bbox[1] + line.bbox[3]) / 2.0 for line in find(row_header)
            ] if row_header else []

            for c, value in enumerate(row):
                if c == 0 or not value or not matrixlib.is_payoff(value):
                    continue
                checked += 1
                found = find(value)
                placed = any(
                    (
                        not row_ys
                        or any(
                            abs((line.bbox[1] + line.bbox[3]) / 2.0 - y)
                            <= matrixlib.ROW_TOLERANCE
                            for y in row_ys
                        )
                    )
                    and (
                        not col_centres.get(c)
                        or any(
                            abs((line.bbox[0] + line.bbox[2]) / 2.0 - x)
                            <= matrixlib.COLUMN_TOLERANCE
                            for x in col_centres[c]
                        )
                    )
                    for line in found
                )
                if not placed:
                    violations.append(
                        {
                            "page": block["page"],
                            "label": block.get("label"),
                            "row": row_header,
                            "column": cells[0][c] if c < len(cells[0]) else "?",
                            "value": value,
                            "issue": "not_found" if not found else "wrong_position",
                        }
                    )

    return {
        "matrices": len(matrices),
        "cells_checked": checked,
        "violations": violations[:40],
        "ok": not violations,
    }


def _canonical_payoff(cell: str) -> str:
    """Compare payoffs by value, not by typography."""
    parts = [p.strip() for p in cell.split(",")]
    canonical = []
    for part in parts:
        part = part.replace("\u2212", "-").replace("\u2013", "-").lstrip("+")
        if part.startswith("-."):
            part = "-0" + part[1:]
        elif part.startswith("."):
            part = "0" + part
        canonical.append(part)
    return ", ".join(canonical)


def gate_xrefs(
    links: list[tuple[str, str | None]],
    index,
    deferred: list[dict],
    in_scope_chapters: set[int],
) -> Gate:
    """Every link in the emitted chapter lands on a route and anchor that exist.

    References whose target chapter has not been emitted yet are reported as
    `deferred` rather than failed: on a partial build those targets genuinely
    do not exist, and linking to them would make the site unbuildable. They are
    still surfaced in the review queue, and once every chapter is emitted the
    deferred list must be empty for the book to be considered complete.
    """
    broken = [
        {"route": route, "anchor": anchor}
        for route, anchor in links
        if not index.resolve(route, anchor)
    ]

    # A reference left unlinked whose target *is* in scope is a genuine drop.
    wrongly_deferred = [
        item
        for item in deferred
        if _target_chapter(item.get("label", "")) in in_scope_chapters
    ]

    detail = {
        "links": len(links),
        "routes_known": len(index.routes),
        "anchors_known": sum(len(a) for a in index.anchors.values()),
        "broken": broken[:60],
        "broken_count": len(broken),
        "deferred_count": len(deferred),
        "deferred_sample": deferred[:20],
        "wrongly_deferred": wrongly_deferred[:20],
    }
    status = PASS if not broken and not wrongly_deferred else FAIL
    return Gate("xrefs", status, detail)


def _target_chapter(label: str) -> int | None:
    """Chapter number a reference label points into, if it is determinable.

    Exercise labels are excluded: an exercise is numbered relative to its own
    chapter ("Exercise 2"), not chapter-qualified, so its number is never a
    target chapter -- treating it as one would misidentify e.g. "Exercise 2"
    inside chapter 17 as pointing at chapter 2, which happens to be emitted,
    and wrongly fail the gate on a reference that was never linkable to begin
    with (the registry carries no per-exercise anchors at all).
    """
    match = re.match(
        r"^(?:Chapter|Section|Figure|Table|Equation)\s+\(?(\d+)", label
    )
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------
# Gate 7 - site build
# --------------------------------------------------------------------------


def gate_build(site_dir: Path, run_build: bool = True) -> Gate:
    """`npm run build` must exit 0 with no warnings."""
    if not (site_dir / "package.json").exists():
        return _blocked("build", "site/ has no package.json yet")
    if not run_build:
        return Gate("build", BLOCKED, {"reason": "build not requested for this run"})
    if not (site_dir / "node_modules").exists():
        return _blocked("build", "site dependencies not installed")

    proc = subprocess.run(
        ["npm", "run", "build"], cwd=site_dir, capture_output=True, text=True
    )
    output = proc.stdout + proc.stderr
    warnings = [
        line for line in output.splitlines() if re.search(r"\bwarn(ing)?\b", line, re.I)
    ]
    detail = {
        "exit_code": proc.returncode,
        "warnings": warnings[:40],
        "warning_count": len(warnings),
    }
    if proc.returncode != 0:
        detail["tail"] = output[-2000:]
    status = PASS if proc.returncode == 0 and not warnings else FAIL
    return Gate("build", status, detail)


# --------------------------------------------------------------------------
# Gate 8 - visual spot check (soft, sampled)
# --------------------------------------------------------------------------


def gate_visual(checked: list[int], flagged: list[int], sampled: list[int]) -> Gate:
    """Soft gate: records which pages were adjudicated by eye."""
    outstanding = sorted(set(flagged) - set(checked))
    detail = {
        "pages_checked": sorted(checked),
        "pages_flagged": sorted(flagged),
        "pages_sampled": sorted(sampled),
        "outstanding": outstanding,
    }
    status = REVIEWED if not outstanding else FAIL
    return Gate("visual", status, detail, hard=False)
