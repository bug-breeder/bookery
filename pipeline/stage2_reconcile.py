"""Stage 2 - reconcile the two extractions into the canonical model.

Text content comes from the PDF's own text layer, and structure comes from
the two extractors. That split is deliberate and specific to born-digital
PDFs like this one: the text layer holds the exact characters the authors
typeset, while Marker and Docling are far better at deciding what is a
heading, a figure, or a caption. Taking prose from a model that can silently
return an empty block -- which Marker does for this book's sub-captions --
would throw away characters we already have.

The extractors therefore act as structural sources *and* as validators: where
they disagree with each other or with the assembled text, the block is
flagged and routed to visual adjudication rather than resolved by guesswork.

For a scanned PDF with no reliable text layer this inverts, and the model
text becomes primary. That path is not exercised by this fixture and is
recorded as a limitation in the README.

Run:  python -m pipeline.stage2_reconcile --chapter 2
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import fitz
from rapidfuzz import fuzz

from . import config, equations as equationslib, furniture, matrix as matrixlib, pdfutil, textnorm
from .model import (
    MATRIX,
    FLAG_CONTENT_DISAGREEMENT,
    FLAG_EMPTY_TEXT,
    FLAG_MISSING_IN_DOCLING,
    FLAG_MISSING_IN_MARKER,
    FLAG_NEEDS_VISUAL,
    FLAG_NUMERIC_DISAGREEMENT,
    FLAG_UNRESOLVED_CAPTION,
    Block,
    ChapterDoc,
)

RE_TAG = re.compile(r"<[^>]+>")
# The separator after the label varies by book/publisher -- "Figure 1.1:" in
# some, "Figure 1.1." in others -- so both are accepted here.
RE_FIGURE_CAPTION = re.compile(r"^(Figure|Table)\s+(\d+\.\d+)\s*[.:]\s*(.*)$", re.DOTALL)
RE_SUBCAPTION = re.compile(r"^\(([a-z])\)\s+(.*)$")
RE_SECTION_HEADING = re.compile(r"^(\d+\.\d+)\s+(\S.*)$")
RE_EXERCISE_START = re.compile(r"^(\d{1,2})\.\s+\S")
# A footnote body's own opening line: a bare number, matching
# `verify/reference.py`'s independent ground-truth count so the structural
# gate compares like for like.
RE_FOOTNOTE_START = re.compile(r"^\d{1,2}\s")

# Cross-references that must resolve to a live anchor. Equation references
# are the one kind printed with the number in parentheses ("Equation
# (14.1)"), not bare after the keyword like the others.
RE_XREF = re.compile(
    r"\b(Chapter|Section|Figure|Table|Exercise)\s+(\d+(?:\.\d+)?)\b"
    r"|\b(Equation)\s+\(?(\d+\.\d+)\)?"
)

AGREEMENT_FLOOR = 0.95


# --------------------------------------------------------------------------
# Loading the two extractions
# --------------------------------------------------------------------------


@dataclass
class Region:
    page: int
    bbox: fitz.Rect
    kind: str
    source: str
    text: str = ""
    # Identifies the extractor item this region came from. One item can yield
    # several regions -- Docling records a separate provenance box per page for
    # a paragraph that straddles a page break, and each box carries the item's
    # whole text -- so anything counting text must count each key once.
    key: str = ""


def _html_text(html: str | None) -> str:
    if not html:
        return ""
    text = RE_TAG.sub(" ", html)
    return re.sub(r"\s+", " ", text).strip()


def load_marker(chapter: int) -> tuple[list[Region], dict]:
    path = config.EXTRACT_DIR / "marker" / f"{config.chapter_id(chapter)}.json"
    if not path.exists():
        raise SystemExit(f"FATAL: {path} missing. Run stage1 first.")
    payload = json.loads(path.read_text())
    regions: list[Region] = []

    def walk(node: dict, page: int | None) -> None:
        block_id = node.get("id") or ""
        parts = block_id.split("/")
        if len(parts) > 2 and parts[1] == "page":
            page = int(parts[2]) + 1  # marker pages are 0-indexed
        bbox = node.get("bbox")
        btype = node.get("block_type")
        if bbox and btype and btype not in ("Page", "Document"):
            regions.append(
                Region(
                    page=page or 0,
                    bbox=fitz.Rect(*bbox),
                    kind=btype,
                    source="marker",
                    text=_html_text(node.get("html")),
                    key=block_id,
                )
            )
        for child in node.get("children") or []:
            walk(child, page)

    walk(payload["document"], None)
    return regions, payload


def load_docling(chapter: int) -> tuple[list[Region], dict]:
    path = config.EXTRACT_DIR / "docling" / f"{config.chapter_id(chapter)}.json"
    if not path.exists():
        raise SystemExit(f"FATAL: {path} missing. Run stage1 first.")
    payload = json.loads(path.read_text())
    doc = payload["document"]
    page_heights = {
        int(k): v["size"]["height"] for k, v in (doc.get("pages") or {}).items()
    }
    regions: list[Region] = []

    def add(item: dict, kind: str) -> None:
        for prov in item.get("prov") or []:
            page = prov.get("page_no")
            bbox = prov.get("bbox")
            if not page or not bbox:
                continue
            height = page_heights.get(page, 792.0)
            if bbox.get("coord_origin") == "BOTTOMLEFT":
                y0, y1 = height - bbox["t"], height - bbox["b"]
            else:
                y0, y1 = bbox["t"], bbox["b"]
            regions.append(
                Region(
                    page=page,
                    bbox=fitz.Rect(bbox["l"], y0, bbox["r"], y1),
                    kind=kind,
                    source="docling",
                    text=(item.get("text") or "").strip(),
                    key=item.get("self_ref") or "",
                )
            )

    for item in doc.get("texts") or []:
        add(item, item.get("label") or "text")
    for item in doc.get("pictures") or []:
        add(item, "picture")
    for item in doc.get("tables") or []:
        add(item, "table")

    return regions, payload


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

MARKER_FIGURE_KINDS = {
    "Diagram",
    "Picture",
    "Figure",
    "FigureGroup",
    "PictureGroup",
    # A handful of exhibits are genuine bordered TeX tabulars (Chapter 23's
    # ranking profiles) but the book still captions them "Figure N.N", never
    # "Table N.N" -- see RE_FIGURE_CAPTION and _bind_captions, which bind by
    # caption text, not by which extractor block supplied the bbox. Without
    # this, Marker's own detected table region is discarded and the exhibit
    # falls through to the paragraph-recovery heuristic, or is lost outright
    # when its rows exceed that heuristic's word limit.
    "Table",
    "TableGroup",
}


def _overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    if not a.intersects(b):
        return 0.0
    inter = (a & b).get_area()
    smaller = min(a.get_area(), b.get_area()) or 1.0
    return inter / smaller


def _is_rule_artifact(bbox: fitz.Rect) -> bool:
    """True for a sliver a layout model mistook for a picture or table.

    A running header/footer divider or a table's own border is drawn as a
    vector line, and both Marker and Docling occasionally box just that line
    as a "picture" or "table" region. A real figure, however small, is
    roughly as tall as it is wide; a rule is not. Every misdetection found
    across a 23-chapter fixture ran 280-450pt in one dimension while under
    15pt in the other -- a 25:1+ aspect ratio no genuine diagram approaches,
    including the smallest legitimate figures. Left unfiltered, one of these
    became a real figure's own label: the caption binder chose the header
    rule over the actual chart below it because both sat above the caption
    and the rule came first in that page's block order.

    A second book's misdetected header line ran only 168pt long (a short
    running head: "132  3 Mixed-Strategy Equilibrium", not the first
    fixture's full-width divider), giving a 17:1 ratio that the 25:1 bar
    misses entirely even though its short side -- under 10pt, barely a
    single text line -- is just as characteristic of a rule as the original
    cases. Below 12pt, the bar relaxes to 12:1: still well above anything a
    genuine diagram's narrowest dimension would need to be to still read as
    a figure, so this doesn't risk swallowing an intentionally thin chart.
    """
    width, height = bbox.width, bbox.height
    short, long = min(width, height), max(width, height)
    if short < 12.0:
        return long > 12 * short
    return short < 15.0 and long > 25 * short


def _merge_figure_regions(regions: list[Region]) -> list[Region]:
    """Union overlapping figure regions proposed by either extractor."""
    figures = [
        r
        for r in regions
        if (r.source == "docling" and r.kind in ("picture", "table"))
        or (r.source == "marker" and r.kind in MARKER_FIGURE_KINDS)
    ]
    merged: list[Region] = []
    for region in sorted(figures, key=lambda r: (r.page, r.bbox.y0)):
        for existing in merged:
            if existing.page == region.page and _overlap_ratio(existing.bbox, region.bbox) > 0.3:
                existing.bbox |= region.bbox
                if region.source not in existing.source:
                    existing.source += "+" + region.source
                break
        else:
            merged.append(
                Region(region.page, fitz.Rect(region.bbox), "figure", region.source)
            )
    return [r for r in merged if not _is_rule_artifact(r.bbox)]


def reconcile_chapter(chapter: int, pdf: Path) -> ChapterDoc:
    triage = json.loads(config.TRIAGE_JSON.read_text())
    meta = next(
        c for c in triage["boundary_map"]["chapters"] if c["number"] == chapter
    )
    offset = triage["boundary_map"]["printed_to_pdf_offset"]
    first, last = meta["pdf_page"], meta["pdf_page_end"]

    marker_regions, marker_payload = load_marker(chapter)
    docling_regions, _ = load_docling(chapter)
    figure_regions = _merge_figure_regions(marker_regions + docling_regions)

    # Display equations come from Marker alone -- see pipeline/equations.py
    # for why the usual "text from the PDF layer" rule inverts for them.
    chapter_equations = equationslib.extract_equations(marker_payload)
    equations_by_page: dict[int, list[equationslib.Equation]] = {}
    for eq in chapter_equations:
        equations_by_page.setdefault(eq.page, []).append(eq)

    doc = fitz.open(pdf)
    chapter_text = "\n".join(doc[p - 1].get_text() for p in range(first, last + 1))
    known_hyphens = textnorm.collect_hyphenated_forms(chapter_text)

    page_furniture = furniture.load_or_detect(doc, triage)
    body_size = furniture.body_size_for(doc, first, last)

    marker_text_boxes = [
        r for r in marker_regions if r.source == "marker" and r.kind in ("Text", "ListGroup", "ListItem")
    ]
    # Captions run to several lines. Only the first carries the "Figure N.N:"
    # label, so without the extractors' caption extents the remainder is
    # emitted as body prose sitting loose after the figure.
    caption_boxes = [
        r
        for r in marker_regions + docling_regions
        if r.kind in ("Caption", "caption")
    ]
    result = ChapterDoc(
        chapter=chapter,
        title=meta["title"],
        part=meta.get("part"),
        pages=(first, last),
        printed_pages=(meta["printed_page"], meta["printed_page"] + (last - first)),
        degraded=json.loads(
            (config.EXTRACT_DIR / "marker" / f"{config.chapter_id(chapter)}.json").read_text()
        ).get("degraded", False),
    )
    result.degraded_reason = marker_payload.get("degraded_reason")

    blocks: list[Block] = []
    counter = 0

    # Exercises are a numbered list running to the end of the chapter. They are
    # only recognised inside the Exercises section, and only when the marker is
    # the next integer in sequence, so numbered prose elsewhere is not mistaken
    # for one.
    in_exercises = False
    exercise_number = 0

    def new_id() -> str:
        nonlocal counter
        counter += 1
        return f"{config.chapter_id(chapter)}-b{counter:04d}"

    for page_no in range(first, last + 1):
        page = doc[page_no - 1]
        height = page.rect.height
        page_figures = [r for r in figure_regions if r.page == page_no]
        untouched, consolidated = pdfutil.reflow_grid_subcaptions(
            pdfutil.page_lines(doc, page_no, rows=False), body_size, known_hyphens
        )
        rows = sorted(
            pdfutil.merge_rows(untouched) + consolidated,
            key=lambda l: (round(l.y0, 1), l.bbox[0]),
        )

        # Payoff matrices are recovered from geometry, because there is nothing
        # else to recover them from: TeX draws them with rules rather than as a
        # tabular environment, so neither extractor reports a table here.
        # Caption positions are passed in so a one-cell game, left over after
        # iterated deletion, can be told apart from an inline number pair.
        # Both edges are passed because a Figure's caption sits below its grid
        # (matched against the grid's bottom) while a Table's sits above it
        # (matched against the grid's top).
        caption_tops = tuple(
            r.bbox[1] for r in rows if RE_FIGURE_CAPTION.match(r.text)
        )
        caption_bottoms = tuple(
            r.bbox[3]
            for r in rows
            if (m := RE_FIGURE_CAPTION.match(r.text)) and m.group(1).lower() == "table"
        )
        page_matrices = matrixlib.find_matrices(
            pdfutil.page_lines(doc, page_no, rows=False),
            page_no,
            caption_tops,
            caption_bottoms,
        )
        page_equations = equations_by_page.get(page_no, [])

        # Where a matrix accounts for almost all of a region an extractor
        # called a picture, the "picture" is the matrix's own rules. Emitting
        # both would show the same payoffs twice: once as a crop whose text is
        # excluded from the reference, and once as a table whose text is not.
        # A Marker "Table" region padded out to the caption dilutes the area
        # ratio below 0.5 even though the matrix is still the whole of it, so
        # containment is also checked from the matrix's own side: nearly all
        # of the matrix sitting inside the region is sufficient on its own.
        page_figures = [
            fig
            for fig in page_figures
            if not any(
                (
                    _area(m.bbox) > 0.5 * _area(tuple(fig.bbox))
                    and _overlap_ratio(fitz.Rect(*m.bbox), fig.bbox) > 0.5
                )
                or _overlap_ratio(fitz.Rect(*m.bbox), fig.bbox) > 0.85
                for m in page_matrices
            )
        ]

        # Marker's fast-mode math OCR occasionally misreads a small payoff
        # grid as a single giant `\begin{array}{c|ccc...}` with no matching
        # `\end{array}` and no row content at all -- garbage LaTeX that would
        # fail Gate 5 for no reason, since the same region is already covered
        # properly by our own PDF-text-layer matrix detection. Where a
        # matrix's bbox accounts for most of an "equation" candidate's area,
        # the equation is that misreading and is dropped in favor of the
        # matrix.
        page_equations = [
            eq
            for eq in page_equations
            if not any(_overlap_ratio(fitz.Rect(*m.bbox), fitz.Rect(*eq.bbox)) > 0.5 for m in page_matrices)
        ]

        pending: list[pdfutil.Line] = []
        pending_kind: str | None = None
        caption_box: Region | None = None
        page_caption_boxes = [r for r in caption_boxes if r.page == page_no]
        page_blocks: list[Block] = []

        def flush() -> None:
            nonlocal pending, pending_kind
            if not pending:
                return
            text = pending[0].text
            for nxt in pending[1:]:
                text = textnorm.join_hyphenated(text, nxt.text, known_hyphens)
            text = textnorm.for_output(text).strip()
            if text:
                bbox = (
                    min(l.bbox[0] for l in pending),
                    min(l.bbox[1] for l in pending),
                    max(l.bbox[2] for l in pending),
                    max(l.bbox[3] for l in pending),
                )
                if pending_kind:
                    kind = pending_kind
                elif in_exercises and RE_EXERCISE_START.match(text):
                    kind = "exercise"
                else:
                    kind = "paragraph"

                label = None
                label_kind = None
                caption_body = None
                anchor = None
                level = None
                if kind == "caption":
                    match = RE_FIGURE_CAPTION.match(text)
                    if match:
                        label_kind = match.group(1).lower()
                        label = match.group(2)
                        caption_body = textnorm.for_output(match.group(3).strip())
                elif kind == "heading":
                    # Parsed from the joined text, not the first line: a
                    # heading long enough to wrap has its title split across
                    # lines, and one of this chapter's breaks falls inside a
                    # hyphenated word ("Empirical Anal-/ysis").
                    match = RE_SECTION_HEADING.match(text)
                    if match:
                        label = match.group(1)
                        anchor = f"sec-{label.replace('.', '-')}"
                    level = 2

                page_blocks.append(
                    Block(
                        id=new_id(),
                        type=kind,
                        page=page_no,
                        text=text,
                        source="textlayer",
                        bbox=bbox,
                        label=label,
                        label_kind=label_kind,
                        level=level,
                        anchor=anchor,
                        caption=caption_body,
                        small_caps=sorted({w for l in pending for w in l.small_caps}),
                    )
                )
            pending = []
            pending_kind = None

        for row in rows:
            rel_top = row.y0 / height
            rect = fitz.Rect(*row.bbox)

            # `Furniture.reason_for` is purely positional/recurrence-based --
            # it has no notion of a line's own content -- so a genuine
            # caption that happens to print inside the page's header zone
            # (because the table or figure it labels is tall enough to push
            # the caption itself up near the top edge) is otherwise
            # indistinguishable from an actual running head at that same
            # y-position, and gets silently dropped before this loop ever
            # reaches the caption-matching logic below. Checked first, and
            # unconditionally exempted, because a caption's text is unique
            # per page and can never truly be the recurring boilerplate
            # `reason_for` is built to catch.
            furniture_reason = (
                None
                if RE_FIGURE_CAPTION.match(row.text)
                else page_furniture.reason_for(
                    row.text, row.y0, row.y1, row.size, body_size, height
                )
            )
            if furniture_reason:
                result.dropped.append(
                    {"page": page_no, "reason": furniture_reason, "text": row.text}
                )
                continue

            caption_match = RE_FIGURE_CAPTION.match(row.text)
            # A caption line always starts a new block; a body sentence that
            # merely wraps onto a line beginning "Figure N.N:" ("...pictured
            # in / Figure 19.23: a node whose...") does not. Both match the
            # same regex, so the wrap is told apart by checking whether this
            # row continues the same extractor text block as the row before
            # it -- a real caption never does, because captions are their own
            # region (or, lacking one, follow a vertical gap after a figure).
            # Gated to `pending_kind is None`: that gap-based fallback sizes
            # its threshold off the previous row alone, and a sub-caption
            # already accumulating its own wrapped continuation (pending_kind
            # == "caption") is taller than one row, so a genuinely new
            # "Figure N.N:" caption starting a normal line-height below it
            # can read as still within the fallback's gap. A pending caption
            # or heading has its own continuation test just below that
            # already decides this correctly from the extractor's own
            # regions; this fallback is only for prose that has not yet been
            # typed at all.
            if (
                caption_match
                and pending
                and pending_kind is None
                and not _crosses_block_boundary(pending[-1], row, marker_text_boxes, page_no)
            ):
                caption_match = None
            # "(a) ..." opens either a figure sub-caption or an exercise
            # sub-part. Sub-captions are set smaller than body text; exercise
            # sub-parts are body size. Without this test every exercise
            # sub-part is styled as a caption.
            sub_match = (
                RE_SUBCAPTION.match(row.text) if row.size < body_size - 1.0 else None
            )
            exercise_part = (
                RE_SUBCAPTION.match(row.text)
                if in_exercises and row.size >= body_size - 1.0
                else None
            )

            inside_figure = any(
                _overlap_ratio(rect, fig.bbox) > 0.6 for fig in page_figures
            )

            # Captions and sub-captions are content even when they fall inside
            # the figure's drawing extent.
            if caption_match:
                flush()
                pending_kind = "caption"
                caption_box = _containing_box(rect, page_caption_boxes)
                pending.append(row)
                continue

            if pending_kind == "caption":
                if caption_box is not None and _overlap_ratio(rect, caption_box.bbox) > 0.5:
                    pending.append(row)
                    continue
                flush()
                caption_box = None

            # A matrix's own rows are carried by its cells, so they must not
            # also accumulate into the surrounding prose. Containment is
            # tested first: matrix rows are narrow and centred, while body
            # text spans the full column, so a wide paragraph line is never
            # mistaken for part of the grid.
            if any(_contains(m.bbox, row.bbox) for m in page_matrices):
                flush()
                continue

            # A row-side player label ("Player 1") sitting just outside the
            # grid's left edge but on the same baseline as its first data row
            # is fused onto that row by ordinary same-line merging, which
            # grows the merged row wide enough to fail strict containment
            # even though the row is still overwhelmingly the grid's own
            # content. A high overlap ratio against the matrix -- gated on
            # the row's height being grid-row-sized, well under the matrix's
            # own height, so an unrelated full-column paragraph line merely
            # passing near a matrix's edge is not swallowed by this fallback
            # -- catches that fusion. The label itself is not thrown away:
            # it sits entirely to one side of the matrix's own x-range, so
            # re-querying the PDF for words confined to that margin recovers
            # it as its own small block instead of losing it along with the
            # grid text it was wrongly merged with.
            fused_matrix = next(
                (
                    m
                    for m in page_matrices
                    if _overlap_ratio(fitz.Rect(*m.bbox), rect) > 0.5
                    and (row.bbox[3] - row.bbox[1]) < 0.6 * (m.bbox[3] - m.bbox[1])
                ),
                None,
            )
            if fused_matrix is not None:
                flush()
                margin = fitz.Rect(row.bbox[0], row.bbox[1], fused_matrix.bbox[0], row.bbox[3])
                if margin.x1 > margin.x0:
                    label_text = page.get_text("text", clip=margin).strip()
                    if label_text:
                        page_blocks.append(
                            Block(
                                id=new_id(),
                                type="paragraph",
                                page=page_no,
                                text=textnorm.for_output(label_text),
                                source="textlayer",
                                bbox=(margin.x0, row.bbox[1], margin.x1, row.bbox[3]),
                            )
                        )
                continue

            # A display equation's own row -- including its right-margin
            # "(14.1)" label, which row-merging unifies with the expression
            # on the same visual line -- is carried by the equation block
            # built from Marker's LaTeX, not by the surrounding prose.
            if any(_contains(eq.bbox, row.bbox) for eq in page_equations):
                flush()
                continue

            if inside_figure and not sub_match:
                # Figure interior: node labels, axis ticks, matrix cells.
                for fig in page_figures:
                    if _overlap_ratio(rect, fig.bbox) > 0.6:
                        fig.text += (" " if fig.text else "") + row.text
                        break
                continue

            if sub_match:
                # A sub-caption wraps to a second physical line whenever it
                # runs long enough on its own ("(e) After four steps:
                # Equilibrium is reached. (Potential" / "energy is 20.)"),
                # not only when it shares a multi-column grid row with a
                # sibling -- `pdfutil.reflow_grid_subcaptions` above only
                # ever looks for a continuation among rows with 2+
                # simultaneous openers, so a lone one reaches here still
                # needing one. Handled by the same pending/caption_box
                # mechanism as a "Figure N.N:" caption, keyed off the same
                # extractor caption region, rather than emitting the block
                # immediately and losing whatever follows on the next line.
                flush()
                pending_kind = "caption"
                caption_box = _containing_box(rect, page_caption_boxes)
                pending.append(row)
                continue

            heading_match = RE_SECTION_HEADING.match(row.text)
            is_display_size = row.size > body_size + 1.5
            if is_display_size and heading_match:
                flush()
                pending_kind = "heading"
                pending.append(row)
                if "Exercises" in row.text:
                    in_exercises = True
                continue

            # A heading that wraps continues on the next line at the same size.
            # Ending the heading at its first line left the remainder as body
            # prose and, where the break fell inside a word, split the word.
            if pending_kind == "heading":
                if is_display_size:
                    pending.append(row)
                    continue
                flush()

            # A footnote body is set a little smaller than body text and sits
            # at the foot of the page -- both conditions are needed, or a
            # small caption or node label elsewhere on the page would count
            # too. Matched against the same shape `verify/reference.py` uses
            # for its independent ground-truth count, so a footnote here is
            # exactly a footnote there.
            is_footnote_size = body_size - 3.5 <= row.size < body_size - 1.0
            footnote_start = (
                is_footnote_size
                and rel_top > 0.70
                and RE_FOOTNOTE_START.match(row.text)
                and not textnorm.is_integer_soup(row.text)
            )
            if footnote_start:
                flush()
                pending_kind = "footnote"
                pending.append(row)
                continue
            # A footnote that wraps continues on the next line at the same
            # small size, exactly as a heading's wrap does above.
            if pending_kind == "footnote":
                if is_footnote_size and rel_top > 0.55:
                    pending.append(row)
                    continue
                flush()

            if row.size > body_size + 8:
                # Chapter opener lines ("Chapter 2" / "Graphs").
                flush()
                page_blocks.append(
                    Block(
                        id=new_id(),
                        type="heading",
                        page=page_no,
                        text=textnorm.for_output(row.text),
                        source="textlayer",
                        level=1,
                        bbox=row.bbox,
                    )
                )
                continue

            # A new exercise always starts a new block, even when the
            # extractors group the whole list into one region.
            if in_exercises:
                start = RE_EXERCISE_START.match(row.text)
                if start and int(start.group(1)) == exercise_number + 1:
                    flush()
                    exercise_number += 1
                elif exercise_part:
                    flush()
                    pending_kind = "exercise_part"

            # Paragraph accumulation, split on the extractors' block bounds.
            if pending and _crosses_block_boundary(pending[-1], row, marker_text_boxes, page_no):
                flush()
            pending.append(row)

        flush()

        for found in page_matrices:
            page_blocks.append(
                Block(
                    id=new_id(),
                    type=MATRIX,
                    page=page_no,
                    source="textlayer",
                    bbox=found.bbox,
                    cells=[
                        [textnorm.for_output(cell) for cell in row]
                        for row in found.as_table()
                    ],
                )
            )

        for eq in page_equations:
            page_blocks.append(
                Block(
                    id=new_id(),
                    type="equation",
                    page=page_no,
                    source="marker",
                    bbox=eq.bbox,
                    label=eq.label,
                    anchor=eq.anchor,
                    latex=eq.latex,
                )
            )

        # Figures for this page, ordered with the text by vertical position.
        for fig in page_figures:
            page_blocks.append(
                Block(
                    id=new_id(),
                    type="figure",
                    page=page_no,
                    source=fig.source,
                    bbox=tuple(fig.bbox),
                    interior_text=[t for t in [fig.text.strip()] if t],
                )
            )

        page_blocks.sort(key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0))
        blocks.extend(page_blocks)

    result.blocks = blocks
    _bind_captions(result)
    _recover_unbound_figure_captions(result, new_id, doc)
    _score_agreement(result, marker_regions, docling_regions)
    _collect_references(result, offset)
    return result


def _containing_box(rect: fitz.Rect, boxes: list[Region]) -> Region | None:
    """Smallest region that substantially contains a row."""
    best: Region | None = None
    for box in boxes:
        if _overlap_ratio(rect, box.bbox) > 0.5:
            if best is None or _area(box.bbox) < _area(best.bbox):
                best = box
    return best


def _area(bbox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


RE_SPACED_DECIMAL = re.compile(r"(?<=[\s(])\.\s+(?=\d)")


def _repair_decimals(text: str) -> str:
    """Rejoin a decimal point the extractor separated from its digits.

    Docling renders the book's leading-zero-less payoffs as "( . 8)( . 4) = . 32".
    A space-delimited lone dot never occurs in prose, so closing the gap is
    safe, and without it every such payoff reads as a different number on the
    extractor's side than on ours.
    """
    return RE_SPACED_DECIMAL.sub(".", text)


def _contains(outer, inner, slack: float = 2.0) -> bool:
    return (
        inner[0] >= outer[0] - slack
        and inner[1] >= outer[1] - slack
        and inner[2] <= outer[2] + slack
        and inner[3] <= outer[3] + slack
    )


def _crosses_block_boundary(
    previous: pdfutil.Line,
    current: pdfutil.Line,
    boxes: list[Region],
    page: int,
) -> bool:
    """True when two consecutive rows belong to different extractor blocks."""
    page_boxes = [b for b in boxes if b.page == page]
    if not page_boxes:
        # Fall back to a vertical gap larger than a line.
        return current.y0 - previous.y1 > 0.8 * (previous.y1 - previous.y0)

    def owner(line: pdfutil.Line) -> int | None:
        rect = fitz.Rect(*line.bbox)
        best, best_score = None, 0.0
        for index, box in enumerate(page_boxes):
            score = _overlap_ratio(rect, box.bbox)
            if score > best_score:
                best, best_score = index, score
        return best if best_score > 0.5 else None

    a, b = owner(previous), owner(current)
    if a is None or b is None:
        return current.y0 - previous.y1 > 0.8 * (previous.y1 - previous.y0)
    return a != b


def _bind_captions(result: ChapterDoc) -> None:
    """Attach each 'Figure N.M:'/'Table N.M:' caption to the exhibit it describes.

    Matrices compete for captions on equal terms with figures. Most of chapter
    6's captioned exhibits are matrices, and leaving them out of this is what
    left 24 captions unbound with their payoffs loose in the prose.
    """
    figures = [b for b in result.blocks if b.type in ("figure", MATRIX)]
    captions = [b for b in result.blocks if b.type == "caption" and b.label]

    for caption in captions:
        candidates = [f for f in figures if f.page == caption.page and f.caption is None]
        if not candidates:
            candidates = [f for f in figures if f.page == caption.page]

        # A Figure's caption sits below it; a Table's sits above -- standard
        # publishing convention, and this book follows it exactly even though
        # it labels some payoff matrices "Table" rather than "Figure" (see
        # matrix.py). The search direction has to follow the caption's own
        # keyword rather than assume one convention for every captioned
        # exhibit, or a Table's content is never found and its caption goes
        # unresolved.
        #
        # The candidate test and distance metric both tolerate the region
        # already *containing* the caption line, not just sitting cleanly
        # past it: an extractor's own bbox for a table sometimes wraps its
        # caption in at the top (the two get boxed as one region), which
        # makes the table's own top edge land at or even slightly before the
        # caption's -- failing a strict "starts below the caption's bottom"
        # test entirely. Two tables close together on one page (this book's
        # Tables 2.8/2.9) turned that into a caption *swap*: the true match
        # was rejected by the strict test, so the first caption processed
        # fell through to the second table's region instead (the only one
        # still strictly below it), leaving the second caption's own search
        # to fall back to whatever candidate remained -- the first table's.
        # Clamping the gap at zero for an overlapping/containing region
        # means it now wins ties over a merely-nearby region the same way a
        # true zero-gap match should, without changing the ranking for the
        # ordinary (non-overlapping) case at all.
        if caption.label_kind == "table":
            below = [
                f
                for f in candidates
                if f.bbox and caption.bbox and f.bbox[3] >= caption.bbox[3] - 6
            ]
            target = (
                min(below, key=lambda f: max(0.0, f.bbox[1] - caption.bbox[3]))
                if below
                else None
            )
        else:
            above = [
                f
                for f in candidates
                if f.bbox and caption.bbox and f.bbox[1] <= caption.bbox[1] + 6
            ]
            target = (
                min(above, key=lambda f: max(0.0, caption.bbox[1] - f.bbox[3]))
                if above
                else None
            )
        if target is None and candidates:
            target = candidates[0]

        if target is None:
            caption.flags.append(FLAG_UNRESOLVED_CAPTION)
            caption.flags.append(FLAG_NEEDS_VISUAL)
            continue

        target.label = caption.label
        # `caption.caption` is deliberately used bare, not `or caption.text`.
        # A handful of this chapter's exercise figures print only the bare
        # label ("Figure 14.15:") with no descriptive text at all, and
        # `caption.text` is the *whole* matched line, label included --
        # falling back to it duplicated the figure number into its own
        # caption body, both as prose and as a self-referential link.
        target.caption = caption.caption
        target.label_kind = caption.label_kind
        # Figure and Table are independent numbering sequences in this book
        # -- "Figure 2.1" and "Table 2.1" can both exist -- so the anchor
        # prefix has to carry the keyword too, or the two collide on the same
        # anchor and the same cropped-asset filename.
        prefix = "tbl" if caption.label_kind == "table" else "fig"
        target.anchor = f"{prefix}-{caption.label.replace('.', '-')}"
        target.alt = _alt_text(target.caption)
        target.small_caps = list(caption.small_caps)

        # The extractors' picture regions sometimes swallow the caption that
        # sits beside the drawing (below for a Figure, above for a Table).
        # Left alone, that caption's text counts as figure interior and is
        # dropped from both the emitted page and the reference -- a loss no
        # gate can see, because both sides lose it identically. Clipping the
        # edge nearest the caption keeps it as text. Sub-captions inside the
        # drawing are left in place: they label the panels and clipping to
        # them would cut multi-panel figures apart.
        if target.bbox and caption.bbox:
            if caption.label_kind == "table" and caption.bbox[3] < target.bbox[3]:
                target.bbox = (
                    target.bbox[0],
                    max(target.bbox[1], caption.bbox[3] + 6.0),
                    target.bbox[2],
                    target.bbox[3],
                )
            elif caption.label_kind != "table" and caption.bbox[1] > target.bbox[1]:
                target.bbox = (
                    target.bbox[0],
                    target.bbox[1],
                    target.bbox[2],
                    min(target.bbox[3], caption.bbox[1] - 6.0),
                )

    for figure in figures:
        # An unlabelled picture region means an extractor found a drawing the
        # book never captioned, which needs a human. An unlabelled matrix does
        # not: the exercises pose games without captioning them, and 18 of this
        # chapter's matrices are legitimately bare. Their cells are verified by
        # the payoff-cell check instead.
        if figure.label is None and figure.type != MATRIX:
            figure.flags.append(FLAG_NEEDS_VISUAL)
        # The caption-bound branch above already set alt text, falling back
        # to a generic description when the caption itself was empty. An
        # uncaptioned figure never goes through that branch at all, so
        # without this it reaches gate 4 with no alt text -- not because the
        # figure lacks a description, but because it never got the same
        # fallback applied.
        if not figure.alt:
            figure.alt = _alt_text(figure.caption)


_CONTENT_WORD_LIMIT = 40  # a flattened table row or a sub-panel label, not a paragraph


def _is_recoverable_content(block: Block) -> bool:
    if block.type == "paragraph":
        pass
    elif block.type == "caption" and not block.label:
        pass  # an unlabelled sub-panel caption, e.g. "(a) An initial configuration."
    else:
        return False
    text = (block.text or block.caption or "").strip()
    return bool(text) and len(text.split()) <= _CONTENT_WORD_LIMIT


def _embedded_image_above(
    doc: fitz.Document, page: int, ceiling: float, floor: float
) -> fitz.Rect | None:
    """Bounding box of a raster image embedded directly in the PDF page.

    A pure screenshot -- a syntax-highlighted code listing, for instance --
    carries no text layer at all, so it leaves nothing in the ordinary
    text-row assembly for the paragraph-recovery heuristic below to consume:
    there is no flattened text between the caption and whatever precedes it,
    only blank space. PyMuPDF's own image inventory still knows exactly
    where such an image sits on the page, independent of either extractor's
    layout model having missed it, so it is used as a last-resort source of
    the figure's extent when no recoverable text is found.
    """
    best = None
    for info in doc[page - 1].get_image_info():
        bbox = fitz.Rect(info["bbox"])
        if bbox.y1 <= floor + 6.0 and bbox.y0 >= ceiling - 6.0:
            if best is None or bbox.y0 < best.y0:
                best = bbox
    return best


def _recover_unbound_figure_captions(
    result: ChapterDoc, new_id, doc: fitz.Document
) -> None:
    """Crop the page region beside a caption neither extractor ever boxed.

    "Beside" is above the caption for a Figure and below it for a Table,
    following the same convention `_bind_captions` uses.

    Some exhibits are neither a picture nor a TeX-tabular grid: a simulation
    state rendered as a grid of cells (Chapter 4's Schelling model), a plain
    bordered data table (Chapter 23's college rankings), a coordinate-plane
    line drawing (Chapter 19). Marker and Docling's layout models miss all of
    these -- there is no picture region and no recognised table -- so the
    content lands in the ordinary text-layer row assembly as flattened
    paragraph text, and `_bind_captions` leaves the "Figure N.N:" caption
    that names it unbound.

    The flattened paragraphs are themselves exactly the region's content, so
    their combined bounding box is the figure's extent: recrop that region
    from the PDF as an image and remove the paragraphs, so the content is
    represented once, as a picture, rather than twice -- once as an unlinked
    caption and once as prose that reads like table cells run together.

    Consumption stops at a normal prose paragraph (more than
    `_CONTENT_WORD_LIMIT` words): a sub-panel label such as "(a) An initial
    configuration." is short enough to pass and is rightly swept in with the
    panel it names, but the narrative paragraph that introduces the exhibit
    ("...as shown in the table in Figure 16.2.") is not, and stopping there
    is what keeps an entire page of unrelated prose from being pulled into
    the crop when nothing else -- no heading, no other caption -- separates
    it from the table above the caption.

    A screenshot with no text layer at all (Chapter 19's Solidity code
    listing) leaves no paragraph to consume here either, so as a last resort
    the PDF's own embedded-image inventory is checked for a raster image
    sitting in the gap between the caption and whatever precedes it.
    """
    by_page: dict[int, list[Block]] = {}
    for block in result.blocks:
        by_page.setdefault(block.page, []).append(block)

    to_remove: set[str] = set()
    to_add: list[Block] = []
    for page, page_blocks in by_page.items():
        ordered = sorted(
            page_blocks, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0)
        )
        for index, block in enumerate(ordered):
            if (
                block.type != "caption"
                or not block.label
                or FLAG_UNRESOLVED_CAPTION not in block.flags
            ):
                continue
            # A Figure's content sits above its caption; a Table's sits below
            # (see `_bind_captions`), so which neighbour is probed has to
            # follow the same keyword.
            is_table = block.label_kind == "table"
            step = 1 if is_table else -1
            consumed: list[Block] = []
            probe = index + step
            while (
                0 <= probe < len(ordered)
                and ordered[probe].bbox
                and _is_recoverable_content(ordered[probe])
            ):
                consumed.append(ordered[probe])
                probe += step

            if consumed:
                bbox = (
                    min(b.bbox[0] for b in consumed),
                    min(b.bbox[1] for b in consumed),
                    max(b.bbox[2] for b in consumed),
                    max(b.bbox[3] for b in consumed),
                )
            elif is_table:
                floor = ordered[probe].bbox[1] if 0 <= probe < len(ordered) and ordered[probe].bbox else 792.0
                image_bbox = _embedded_image_above(doc, page, block.bbox[3], floor)
                if image_bbox is None:
                    continue
                bbox = (image_bbox.x0, image_bbox.y0, image_bbox.x1, image_bbox.y1)
            else:
                ceiling = ordered[probe].bbox[3] if 0 <= probe < len(ordered) and ordered[probe].bbox else 0.0
                image_bbox = _embedded_image_above(doc, page, ceiling, block.bbox[1])
                if image_bbox is None:
                    continue
                bbox = (image_bbox.x0, image_bbox.y0, image_bbox.x1, image_bbox.y1)

            prefix = "tbl" if is_table else "fig"
            figure = Block(
                id=new_id(),
                type="figure",
                page=page,
                source="recovered",
                bbox=bbox,
                label=block.label,
                label_kind=block.label_kind,
                anchor=f"{prefix}-{block.label.replace('.', '-')}",
                caption=block.caption,
                alt=_alt_text(block.caption),
                flags=[FLAG_NEEDS_VISUAL],
            )
            to_add.append(figure)
            to_remove.update(b.id for b in consumed)
            block.flags = [f for f in block.flags if f != FLAG_UNRESOLVED_CAPTION]

    if to_add:
        result.blocks = [b for b in result.blocks if b.id not in to_remove] + to_add
        result.blocks.sort(key=lambda b: (b.page, b.bbox[1] if b.bbox else 0))


def _alt_text(caption: str | None, limit: int = 240) -> str:
    """Alt text derived from the caption; never left empty.

    Citation markers are dropped: they read as noise to a screen reader, and a
    stray "[" would terminate the Markdown image syntax early, which makes the
    image silently render as literal text.
    """
    if not caption:
        return "Figure from the text; see the surrounding discussion."
    text = re.sub(r"\[\s*\d{1,3}(?:\s*,\s*\d{1,3})*\s*\]", "", caption)
    text = text.replace("[", "").replace("]", "")
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _score_agreement(
    result: ChapterDoc, marker_regions: list[Region], docling_regions: list[Region]
) -> None:
    """Compare assembled text with each extractor and flag divergence."""
    marker_by_page: dict[int, list[Region]] = {}
    docling_by_page: dict[int, list[Region]] = {}
    for region in marker_regions:
        marker_by_page.setdefault(region.page, []).append(region)
    for region in docling_regions:
        docling_by_page.setdefault(region.page, []).append(region)

    for block in result.blocks:
        if block.type not in ("paragraph", "caption", "heading", "exercise", "exercise_part"):
            continue
        if not block.text.strip():
            block.flags.append(FLAG_EMPTY_TEXT)
            block.flags.append(FLAG_NEEDS_VISUAL)
            continue
        if not block.bbox:
            continue

        rect = fitz.Rect(*block.bbox)
        scores: list[float] = []
        for source, index in (("marker", marker_by_page), ("docling", docling_by_page)):
            best_text, best_overlap = "", 0.0
            for region in index.get(block.page, []):
                overlap = _overlap_ratio(rect, region.bbox)
                if overlap > best_overlap:
                    best_text, best_overlap = region.text, overlap
            if best_overlap < 0.3 or not best_text:
                block.flags.append(
                    FLAG_MISSING_IN_MARKER if source == "marker" else FLAG_MISSING_IN_DOCLING
                )
                continue
            ours = textnorm.normalize(block.text)
            theirs = textnorm.normalize(best_text)
            # Extractors merge and split blocks differently, so containment
            # counts as agreement; only genuinely different characters do not.
            if ours and (ours in theirs or theirs in ours):
                scores.append(1.0)
            else:
                scores.append(fuzz.ratio(ours, theirs) / 100.0)

        if scores:
            block.agreement = sum(scores) / len(scores)
            if block.agreement < AGREEMENT_FLOOR:
                block.flags.append(FLAG_CONTENT_DISAGREEMENT)
                block.flags.append(FLAG_NEEDS_VISUAL)

    # Regions the cross-extractor numeric check abstains on. Figure interiors
    # are excluded because our side does not carry them. Matrices are excluded
    # for the opposite reason and it matters just as much: our side has their
    # payoffs and the extractors mostly do not, because TeX draws these grids
    # with rules rather than as tables. Docling omits the cells outright and
    # Marker reports ".48" as "48", so comparing them here reported a
    # disagreement on every matrix page in the chapter -- 43 of them -- none of
    # which meant anything. Matrix payoffs are not left unverified by this:
    # gates 1 and 2 check them against the PDF's own text layer, which is the
    # authority both extractors are failing to match, and the payoff-cell check
    # compares every cell that reaches the page.
    # Equations are excluded for a third reason, distinct from both of the
    # above: their content never came from either extractor's prose reading
    # in the first place, it came from Marker's own Equation block, so a
    # comparison here would just be checking Marker's LaTeX against itself.
    excluded_by_page: dict[int, list[fitz.Rect]] = {}
    for block in result.blocks:
        if block.type in ("figure", MATRIX, "equation") and block.bbox:
            excluded_by_page.setdefault(block.page, []).append(fitz.Rect(*block.bbox))

    _flag_numeric_disagreements(result, marker_by_page, docling_by_page, excluded_by_page)


def _flag_numeric_disagreements(
    result: ChapterDoc,
    marker_by_page: dict[int, list[Region]],
    docling_by_page: dict[int, list[Region]],
    excluded_by_page: dict[int, list[fitz.Rect]],
) -> None:
    """Cross-validate numeric literals against each extractor, chapter-wide.

    Only one direction of this comparison carries information. A number an
    extractor read that appears nowhere in our chapter may be a number we lost,
    and that is worth stopping for. The reverse -- a number we have and an
    extractor does not -- says only that the extractor missed something, which
    on this book it does constantly and for three systematic reasons: Docling
    drops display equations outright, normalises list markers away, and merges
    paragraphs across page breaks. Our text comes from the PDF's own text layer
    and is checked against it directly by gates 1 and 2, so an extractor being
    the poorer reader is not evidence against us.

    The comparison is chapter-wide rather than per page for the same reason.
    Page scope made every paragraph straddling a page break look like a
    disagreement, because the extractors attribute the whole of it to one page
    while we split it at the boundary: 33 reports on chapter 6, not one of them
    a real defect.
    """
    from collections import Counter

    ours = Counter(
        n
        for b in result.blocks
        if b.type
        in ("paragraph", "caption", "heading", "exercise", "exercise_part", "footnote")
        for n in textnorm.numbers(b.text)
    )
    ours += Counter(
        n
        for b in result.blocks
        if b.type == MATRIX and b.cells
        for row in b.cells
        for cell in row
        for n in textnorm.numbers(cell)
    )

    def is_comparable(region: Region) -> bool:
        # Furniture and figure interiors are outside this comparison on our
        # side, so they must be outside it on theirs too, or every page
        # carrying one reports a spurious difference.
        if region.kind in (
            "page_header",
            "page_footer",
            "PageHeader",
            "PageFooter",
            "footnote",
            "Footnote",
            "picture",
            "Picture",
            "Diagram",
            "Figure",
        ):
            return False
        return not any(
            _overlap_ratio(region.bbox, excluded) > 0.6
            for excluded in excluded_by_page.get(region.page, [])
        )

    for source, index in (("marker", marker_by_page), ("docling", docling_by_page)):
        comparable = [r for regions in index.values() for r in regions if is_comparable(r)]
        # Count each extractor item once. Marker nests list items inside a
        # group whose html repeats them, and Docling repeats an item's text for
        # every page it spans, so counting regions rather than items inflates
        # the extractor's side and invents disagreements that are not there.
        seen_keys: set[str] = set()
        deduped: list[Region] = []
        for region in comparable:
            if region.key and region.key in seen_keys:
                continue
            if region.key:
                seen_keys.add(region.key)
            deduped.append(region)
        comparable = [r for r in deduped if not r.kind.endswith("Group")]
        theirs = Counter(
            n for r in comparable for n in textnorm.numbers(_repair_decimals(r.text))
        )
        if not theirs:
            continue
        unseen = theirs - ours
        if not unseen:
            continue

        pages = sorted(
            {
                r.page
                for r in comparable
                if set(textnorm.numbers(r.text)) & set(unseen)
            }
        )
        for block in result.blocks:
            if block.page in pages and block.type == "paragraph":
                if FLAG_NUMERIC_DISAGREEMENT not in block.flags:
                    block.flags.append(FLAG_NUMERIC_DISAGREEMENT)
                    block.flags.append(FLAG_NEEDS_VISUAL)
                break
        result.dropped.append(
            {
                "reason": f"numeric_missing_vs_{source}",
                "pages": pages,
                "only_in_extractor": sorted(unseen.elements()),
            }
        )


def _collect_references(result: ChapterDoc, offset: int) -> None:
    """Record every numbered cross-reference and every anchor defined here."""
    anchors = {b.anchor for b in result.blocks if b.anchor}
    result.anchors = sorted(a for a in anchors if a)

    seen: list[dict] = []
    for block in result.blocks:
        if not block.text:
            continue
        stripped, citations = textnorm.extract_citations(block.text)
        for number in citations:
            seen.append(
                {
                    "kind": "citation",
                    "label": str(number),
                    "target": f"bibliography#ref-{number}",
                    "page": block.page,
                    "block": block.id,
                }
            )
        for kind_a, number_a, kind_b, number_b in RE_XREF.findall(stripped):
            kind, number = (kind_a, number_a) if kind_a else (kind_b, number_b)
            seen.append(
                {
                    "kind": kind.lower(),
                    "label": f"{kind} {number}",
                    "target": _target_for(kind, number, result.chapter),
                    "page": block.page,
                    "block": block.id,
                }
            )
    result.references = seen


def _target_for(kind: str, number: str, chapter: int) -> str:
    slug = number.replace(".", "-")
    kind = kind.lower()
    if kind == "chapter":
        return f"chapter-{number}"
    if kind == "section":
        return f"sec-{slug}"
    if kind == "figure":
        return f"fig-{slug}"
    if kind == "table":
        return f"tbl-{slug}"
    if kind == "equation":
        return f"eq-{slug}"
    return f"ex-{slug}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 2: reconcile extractions")
    ap.add_argument("--chapter", type=int, action="append", required=True)
    ap.add_argument("--pdf", type=Path, default=config.DEFAULT_PDF)
    args = ap.parse_args()

    config.RECONCILE_DIR.mkdir(parents=True, exist_ok=True)
    for number in args.chapter:
        result = reconcile_chapter(number, args.pdf)
        path = config.RECONCILE_DIR / f"{config.chapter_id(number)}.json"
        path.write_text(json.dumps(result.as_dict(), indent=1, ensure_ascii=False))
        counts = result.counts()
        flagged = sum(1 for b in result.blocks if b.flags)
        print(
            f"ch{number:02d}: {len(result.blocks)} blocks, "
            f"{len(counts['figures'])} figures, {len(counts['sections'])} sections, "
            f"{flagged} flagged, {len(result.dropped)} dropped -> {path.name}"
        )


if __name__ == "__main__":
    main()
