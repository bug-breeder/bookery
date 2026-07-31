"""PyMuPDF helpers.

Text is assembled at line level rather than span level. PyMuPDF's span
segmentation varies between releases (1.28 splits on every word boundary,
older builds merge runs), so any logic that matches against span text is
quietly version-dependent. Lines are stable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

import fitz

from . import textnorm


# TeX sets small caps in a dedicated face (Computer Modern Caps and Small
# Caps). Host names such as MIT and BBN are typeset that way, and their text
# layer is lowercase -- "mit" -- which reads as a typo unless it is presented
# in small caps again. Detecting the face is document-driven; a list of known
# host names would not survive contact with the rest of the book.
SMALL_CAPS_FONT_HINTS = ("CSC", "SmallCaps", "SC10", "SC12")


def is_small_caps(font: str) -> bool:
    return any(hint in font for hint in SMALL_CAPS_FONT_HINTS)


@dataclass(frozen=True)
class Line:
    text: str
    size: float  # largest span size on the line
    bbox: tuple[float, float, float, float]
    fonts: tuple[str, ...]
    # Words on this line that the PDF sets in a small-caps face.
    small_caps: tuple[str, ...] = ()

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]


# A horizontal gap wider than this fraction of the font size is a word break.
# Real spaces in this book measure about 0.33em and kerning between letters is
# zero or slightly negative, so the two populations are far apart. The value
# still needs care at the low end: the gap between the minus and the variable
# in "1 - q" is only a little over 0.15em, and raising this to 0.2 silently
# closed it up into "1 -q".
SPACE_GAP_RATIO = 0.15


def _span_text(span: dict) -> str:
    """A span's text, whether it came from "dict" or "rawdict"."""
    if "text" in span:
        return span["text"]
    return "".join(char["c"] for char in span.get("chars") or [])


def _join_chars(spans: list[dict]) -> str:
    """Assemble a line character by character, restoring positional spaces.

    Two different TeX habits require this, and both are invisible at span
    level. A heading's number is separated from its title by a fixed
    horizontal skip rather than a space, so a naive join yields '2.1Basic
    Definitions'. More insidiously, a space following an f-ligature is encoded
    as pure positioning with no space character at all: the text layer holds
    'payoffmatrix' and 'payoffof' where the page plainly reads 'payoff matrix'
    and 'payoff of'. Because the reference text is built from the same text
    layer, no coverage or numeric gate could ever have caught that -- both
    sides were wrong in the same way -- and the words reached the reader fused.

    Working at character level rather than span level catches both, since the
    only reliable evidence of the space is the gap in the glyph positions.
    """
    out: list[str] = []
    previous_x1: float | None = None
    for span in spans:
        size = span["size"]
        chars = span.get("chars")
        if not chars:
            # No per-character detail: fall back to the span's own box.
            text, x0, x1 = span["text"], span["bbox"][0], span["bbox"][2]
            if (
                previous_x1 is not None
                and out
                and not out[-1].endswith(" ")
                and not text.startswith(" ")
                and x0 - previous_x1 > SPACE_GAP_RATIO * size
            ):
                out.append(" ")
            out.append(text)
            previous_x1 = x1
            continue

        for char in chars:
            glyph = char["c"]
            x0, x1 = char["bbox"][0], char["bbox"][2]
            if (
                previous_x1 is not None
                and out
                and not out[-1].endswith(" ")
                and glyph != " "
                and x0 - previous_x1 > SPACE_GAP_RATIO * size
            ):
                out.append(" ")
            out.append(glyph)
            previous_x1 = x1
    return "".join(out)


def iter_lines(page: fitz.Page) -> Iterator[Line]:
    """Yield assembled text lines in layout order.

    Uses "rawdict" rather than "dict" because the per-character positions are
    the only place a space encoded purely as positioning can be recovered from.

    "rawdict" reports every bbox in the page's raw, pre-`/Rotate` content
    space, not the displayed space `page.rect` (and every consumer of a
    `Line.bbox`) assumes -- a whole-page landscape chart embedded sideways
    and rotated back with `/Rotate 90` for display comes back with its
    "horizontal" caption reporting `dir == (0, -1)` and a bbox that is nine
    points wide and forty tall, the transpose of how it actually reads.
    `page.rotation_matrix` is the same matrix `get_pixmap` applies to render
    the page the way it is meant to be read, so applying it here once means
    every position-based heuristic downstream -- row merging, caption
    binding, furniture detection, matrix geometry -- can keep assuming
    bboxes are already in display space.
    """
    rotation_matrix = page.rotation_matrix if page.rotation else None
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = _join_chars(spans).strip()
            if not text:
                continue
            small_caps = tuple(
                word
                for span in spans
                if is_small_caps(span["font"])
                for word in _span_text(span).split()
                if word.strip(".,;:()[]")
            )
            bbox = tuple(line["bbox"])
            if rotation_matrix is not None:
                bbox = tuple(fitz.Rect(*bbox) * rotation_matrix)
            yield Line(
                text=text,
                size=max(s["size"] for s in spans),
                bbox=bbox,
                fonts=tuple(sorted({s["font"] for s in spans})),
                small_caps=small_caps,
            )


def _vertical_overlap(a: Line, b: Line) -> float:
    top, bottom = max(a.y0, b.y0), min(a.y1, b.y1)
    if bottom <= top:
        return 0.0
    shortest = min(a.y1 - a.y0, b.y1 - b.y0) or 1.0
    return (bottom - top) / shortest


def merge_rows(lines: list[Line], min_overlap: float = 0.6) -> list[Line]:
    """Merge lines that occupy the same visual row, left to right.

    TeX emits a section number and its title as separate lines at the same
    baseline, and does the same for a running head and its folio and for the
    cells of a payoff matrix. Treating each visual row as one unit is what
    lets those be recognised as headings, furniture, and table rows rather
    than as unrelated fragments.
    """
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda l: (round(l.y0, 1), l.bbox[0])):
        for row in rows:
            if _vertical_overlap(row[0], line) >= min_overlap:
                row.append(line)
                break
        else:
            rows.append([line])

    merged: list[Line] = []
    for row in rows:
        row.sort(key=lambda l: l.bbox[0])
        if len(row) == 1:
            merged.append(row[0])
            continue
        merged.append(
            Line(
                text=" ".join(l.text for l in row),
                size=max(l.size for l in row),
                bbox=(
                    min(l.bbox[0] for l in row),
                    min(l.bbox[1] for l in row),
                    max(l.bbox[2] for l in row),
                    max(l.bbox[3] for l in row),
                ),
                fonts=tuple(sorted({f for l in row for f in l.fonts})),
                small_caps=tuple(w for l in row for w in l.small_caps),
            )
        )
    return merged


_RE_SUBCAPTION = re.compile(r"^\(([a-z])\)\s+(.*)$")


def reflow_grid_subcaptions(
    lines: list[Line], body_size: float, known_hyphens: set[str]
) -> tuple[list[Line], list[Line]]:
    """Undo a multi-column sub-caption grid's reading order.

    A multi-panel figure sometimes sets its "(a) ...", "(b) ..." captions two
    per row instead of stacked -- Figure 5.1's four triangles, laid out two
    per row with each caption wrapping onto a second line. Reading order for
    that layout is top-to-bottom *within a column*, not left-to-right by
    baseline: the PDF's own line order is opener, opener, continuation,
    continuation, interleaving two unrelated captions ("...en-", "...bal-",
    "emy: balanced.", "anced."). Row-merging by baseline -- correct for a
    section number sharing a line with its title -- splices the two openers
    into one nonsense line here and fuses their continuations into another,
    with no trace left of which continuation belongs to which opener.

    This runs on *unmerged* lines, before `merge_rows`, which is the only
    point where each physical line is still a separate object. The PDF's
    content-stream order is not reading order here: a multi-panel figure's
    vertex/edge labels ("A", "B", "+", ...) are interleaved between the two
    captions' text runs, so a caption's opener and its continuation are not
    adjacent in `lines` even though they sit directly below one another on
    the page. This groups all caption-sized lines by geometry instead --
    clustering into visual rows by baseline, then, starting from each row
    where two or more lines open a sub-caption ("(a) ...", "(b) ..."),
    assigning every following caption-sized row to whichever opener's
    x-column it lines up with -- until the next opener row starts a new
    group. Each column's lines are hyphen-joined into one completed caption.
    Everything else passes through untouched for `merge_rows` to handle as
    usual.

    Shared by `pipeline.stage2_reconcile` (to build the emitted candidate)
    and `verify.reference` (to build the independent reference the candidate
    is checked against): the two must resolve this exact same layout the
    same way, or a caption that wraps mid-grid reads as a spurious extra
    number or a spurious dropped one, having never actually been lost.

    Returns `(untouched, consolidated)` rather than one combined list: a
    consolidated caption's bbox spans both its own baseline and its
    continuation's, at a fraction of the page's width, so `merge_rows`
    checking it against the *other* column's equally tall, equally
    overlapping consolidated caption would merge the two right back into the
    same nonsense line this exists to prevent. The caller merges `untouched`
    on its own and folds `consolidated` in afterwards, already complete.
    """
    small = [line for line in lines if line.size < body_size - 1.0]
    if len(small) < 2:
        return lines, []

    # Grouped by the same vertical-overlap test `merge_rows` itself uses, not
    # a fixed baseline tolerance: two sub-captions set side by side are not
    # always baseline-exact (20.9's "(a)"/"(b)" sit 3.6pt apart), but whenever
    # `merge_rows` would still consider them one row, this must recognise
    # that row too, or the very smashing-together this exists to prevent
    # slips through under a stricter threshold than the thing it guards.
    order = sorted(range(len(small)), key=lambda i: (round(small[i].bbox[1], 1), small[i].bbox[0]))
    groups: list[list[int]] = []
    for idx in order:
        for group in groups:
            if _vertical_overlap(small[group[0]], small[idx]) >= 0.6:
                group.append(idx)
                break
        else:
            groups.append([idx])
    rows: list[tuple[float, list[int]]] = sorted(
        ((min(small[i].bbox[1] for i in g), g) for g in groups), key=lambda r: r[0]
    )

    consolidated: list[Line] = []
    consumed: set[int] = set()
    columns: list[tuple[float, list[int]]] | None = None
    caption_size = 0.0

    def flush() -> None:
        nonlocal columns
        if columns and any(len(idxs) > 1 for _, idxs in columns):
            for _, idxs in columns:
                col = [small[i] for i in idxs]
                text = col[0].text
                for nxt in col[1:]:
                    text = textnorm.join_hyphenated(text, nxt.text, known_hyphens)
                consolidated.append(
                    Line(
                        text=text,
                        size=col[0].size,
                        bbox=(
                            min(l.bbox[0] for l in col),
                            min(l.bbox[1] for l in col),
                            max(l.bbox[2] for l in col),
                            max(l.bbox[3] for l in col),
                        ),
                        fonts=tuple(sorted({f for l in col for f in l.fonts})),
                        small_caps=tuple(w for l in col for w in l.small_caps),
                    )
                )
                consumed.update(idxs)
        columns = None

    for _, idxs in rows:
        row = sorted(idxs, key=lambda i: small[i].bbox[0])
        is_opener_row = len(row) >= 2 and all(
            _RE_SUBCAPTION.match(small[i].text.strip()) for i in row
        )
        if is_opener_row:
            flush()
            columns = [(small[i].bbox[0], [i]) for i in row]
            caption_size = sum(small[i].size for i in row) / len(row)
        elif columns is not None:
            # A multi-panel figure's own diagram (node labels, edge signs,
            # table headers repeating below the caption for the next panel,
            # ...) is drawn at its own font size, distinct from -- if
            # sometimes only a little larger or smaller than -- the caption's;
            # content sharing a caption's x-column but set in a different
            # font size is the figure's artwork, not a caption continuation,
            # and must not be swept in just because it happens to line up. A
            # genuine continuation is set in the exact same face as its
            # opener, so the tolerance here is tight, not merely "close".
            candidates = [i for i in row if abs(small[i].size - caption_size) <= 0.1]
            if not candidates:
                continue
            last_y = max(small[j].bbox[1] for _, idxs2 in columns for j in idxs2)
            if small[candidates[0]].bbox[1] - last_y > 40.0:
                flush()
                continue
            for i in candidates:
                x = small[i].bbox[0]
                best = min(range(len(columns)), key=lambda c: abs(x - columns[c][0]))
                if abs(x - columns[best][0]) <= 20.0:
                    columns[best][1].append(i)
    flush()

    if not consumed:
        return lines, []
    consumed_ids = {id(small[i]) for i in consumed}
    untouched = [line for line in lines if id(line) not in consumed_ids]
    return untouched, consolidated


def grid_opener_rows(lines: list[Line], body_size: float) -> set[float]:
    """Y-positions (rounded to 1dp) of rows with 2+ simultaneous sub-caption openers.

    A multi-panel figure's "(a) ...", "(b) ..." captions share a baseline even
    when only one of them goes on to consolidate a wrapped continuation in
    `reflow_grid_subcaptions` above: `flush` there emits every column once any
    one of them has 2+ lines, so the *other* opener comes out as its own
    untouched-looking single-line `Line`, indistinguishable by text alone from
    an ordinary standalone sub-caption that never shared a row with anything.
    A caller deciding whether a caption opener may own a multi-line
    continuation of its own (`verify.reference`) needs to know it was part of
    such a pair regardless of which path it left this function by, so it
    looks up the pair's shared baseline here instead of pattern-matching the
    opener's text on its own.
    """
    small = [line for line in lines if line.size < body_size - 1.0]
    order = sorted(range(len(small)), key=lambda i: (round(small[i].bbox[1], 1), small[i].bbox[0]))
    groups: list[list[int]] = []
    for idx in order:
        for group in groups:
            if _vertical_overlap(small[group[0]], small[idx]) >= 0.6:
                group.append(idx)
                break
        else:
            groups.append([idx])
    openers = set()
    for group in groups:
        if len(group) >= 2 and all(_RE_SUBCAPTION.match(small[i].text.strip()) for i in group):
            openers.add(round(min(small[i].bbox[1] for i in group), 1))
    return openers


def page_lines(doc: fitz.Document, pdf_page: int, rows: bool = True) -> list[Line]:
    """Lines for a 1-indexed PDF page, merged into visual rows by default."""
    lines = list(iter_lines(doc[pdf_page - 1]))
    return merge_rows(lines) if rows else lines


def first_text_line(doc: fitz.Document, pdf_page: int) -> str:
    text = doc[pdf_page - 1].get_text()
    return next((l.strip() for l in text.splitlines() if l.strip()), "")
