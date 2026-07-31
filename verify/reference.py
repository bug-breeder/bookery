"""Builds the reference text that the gates compare against.

The reference is the PDF's own text layer with page furniture and
figure-internal text removed. Getting those subtractions right is what makes
the coverage and numeric gates meaningful: leave the running heads in and
every chapter fails, strip too eagerly and real prose vanishes unnoticed.

Nothing is dropped silently. Every removed line is returned alongside the
text with the reason it was removed, so the review queue can account for it.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from pipeline import config, furniture, pdfutil, textnorm

# Furniture zone geometry (header/folio bands, footer boilerplate search)
# lives entirely in `pipeline.furniture`, measured per-book rather than
# assumed from a fixed fraction of page height.

# A line must recur on at least this many pages to count as furniture.
MIN_REPEATS = 3

_RE_DIGITS = re.compile(r"\d+")

# Caption openers, and sub-figure labels such as "(a) A graph on 4 nodes."
# Both are content and are never treated as figure interior. The separator
# after the label varies by book -- "Figure 1.1:" in some, "Figure 1.1." in
# others -- so both are accepted here.
RE_CAPTION_START = re.compile(r"^(Figure|Table)\s+\d+\.\d+\s*[.:]|^\([a-z]\)\s+\S")

# A folio is a bare arabic or roman page number and nothing else.
RE_BARE_FOLIO = re.compile(r"^(\d{1,4}|[ivxlcdm]{1,7})$", re.IGNORECASE)

# A numbered display equation's label, printed at the right margin of its
# own row. Row-merging unifies it with the equation's text onto one line
# ("hi <- Mi1a1 + ... , (14.1)"), so it is searched for at line end rather
# than matched against the whole line.
RE_EQUATION_LABEL = re.compile(r"\(\s*(\d+)\.(\d+)\s*\)\s*$")

# A genuine display equation is algebra: mostly variables and operators, with
# at most a coefficient or two written as a bare number. A regression table's
# row, by contrast, is almost nothing *but* bare numbers (estimates, standard
# errors, t-stats) -- and every so often one coincidentally ends in something
# shaped like, and even matching the chapter number of, a real equation label
# ("... (0.01) 0.670 0.85 (4.14)" in chapter 4). Matching the chapter number
# alone still lets these through when they land in the right chapter purely
# by chance; capping how many other bare numbers may share the line catches
# them without needing to recognise the book's own table layouts.
_MAX_DATA_ROW_NUMBERS = 3


def _looks_like_data_row(text: str) -> bool:
    return len(textnorm.numbers(text)) > _MAX_DATA_ROW_NUMBERS



def _raw_rows(page: fitz.Page) -> list[pdfutil.Line]:
    """Page text as merged visual rows, without sub-caption grid reflow.

    Good enough for a page's own font-size statistics and for the structural
    label counts, neither of which cares which column a sub-caption's wrapped
    continuation belongs to.
    """
    return pdfutil.merge_rows(list(pdfutil.iter_lines(page)))


def _rows(page: fitz.Page, body_size: float, known_hyphens: set[str]) -> list[pdfutil.Line]:
    """Page text as merged visual rows.

    Multi-column sub-caption grids are reflowed the same way
    `pipeline.stage2_reconcile` reflows them before its own row-merging, so a
    caption that wraps mid-grid resolves to the same text on both sides of
    the comparison -- see `pdfutil.reflow_grid_subcaptions`.
    """
    untouched, consolidated = pdfutil.reflow_grid_subcaptions(
        list(pdfutil.iter_lines(page)), body_size, known_hyphens
    )
    return sorted(
        pdfutil.merge_rows(untouched) + consolidated,
        key=lambda l: (round(l.y0, 1), l.bbox[0]),
    )


def _mask(text: str) -> str:
    """Digit-masked key so 'CHAPTER 2. GRAPHS 24' and '... 25' group together."""
    return _RE_DIGITS.sub("#", textnorm.normalize(text))


@dataclass
class RemovedLine:
    page: int
    text: str
    reason: str
    bbox: tuple[float, float, float, float]


@dataclass
class Reference:
    """Reference text for a page range, plus a full account of what was cut."""

    pages: tuple[int, int]
    body_text: str
    body_by_page: dict[int, str]
    removed: list[RemovedLine] = field(default_factory=list)

    @property
    def tokens(self) -> list[str]:
        return textnorm.tokens(self.body_text)

    def removed_by_reason(self) -> dict[str, int]:
        counts: Counter[str] = Counter(r.reason for r in self.removed)
        return dict(counts)


def _classify(
    line: pdfutil.Line,
    height: float,
    page_furniture: furniture.Furniture,
    body_size: float,
    figure_rects: list[fitz.Rect],
    equation_rects: list[fitz.Rect] = (),
) -> str | None:
    """Return a removal reason, or None to keep the line."""
    # A caption is content even when it geometrically lands somewhere
    # furniture normally lives -- inside a figure's drawing extent (a
    # diagram's vectors overrunning the text column), or, just as real,
    # inside the page's positional header zone whenever the table/figure it
    # labels is tall enough to push the caption itself up near the top edge.
    # `Furniture.reason_for` is purely positional/recurrence-based and has
    # no way to tell a genuine one-off caption from the running head it
    # shares a y-position with, so this must be checked, and win, before
    # that furniture reason is ever consulted.
    if RE_CAPTION_START.match(line.text):
        return None

    reason = page_furniture.reason_for(
        line.text, line.y0, line.y1, line.size, body_size, height
    )
    if reason:
        return reason

    # Below `display_heading_threshold` but still oversized enough that
    # `stage2_reconcile` reads it as the chapter's own level-1 heading, which
    # `stage4_emit` then never prints in the body -- the frontmatter title
    # already carries it. Left in the reference, its words and any digits in
    # it (a chapter number, or a title like "Blockchain 101") would be
    # credited against a candidate that represents them exactly once, via
    # that frontmatter line, not twice.
    if furniture.is_chapter_opener_size(line.size, body_size):
        return "chapter_opener_heading"

    if figure_rects:
        rect = fitz.Rect(*line.bbox)
        for fig in figure_rects:
            if fig.contains(rect) or (rect & fig).get_area() > 0.6 * rect.get_area():
                return "figure_interior"

    if equation_rects:
        # Strict near-full containment, deliberately not the >60%-overlap
        # test used for figure interiors just above. That looser test is
        # right for a vector diagram's large bbox with axis-tick text well
        # inside it, but an equation's bbox is drawn tight around a single
        # row -- the same row-merging that unifies "hi <- ..." with its
        # "(14.1)" label can also just touch the top or bottom of an
        # adjacent full-width paragraph line. This must match the
        # containment test `pipeline.stage2_reconcile` uses to decide which
        # rows an equation block already carries, on the same slack budget,
        # or a row can be excluded here while still reaching the candidate
        # as ordinary prose (or the reverse) and the two sides disagree on
        # a token neither pipeline actually dropped.
        rect = fitz.Rect(*line.bbox)
        slack = 2.0
        for eq in equation_rects:
            if (
                rect.x0 >= eq.x0 - slack
                and rect.y0 >= eq.y0 - slack
                and rect.x1 <= eq.x1 + slack
                and rect.y1 <= eq.y1 + slack
            ):
                return "equation_interior"

    if textnorm.is_integer_soup(line.text):
        return "integer_soup"

    return None


def build_reference(
    doc: fitz.Document,
    first_page: int,
    last_page: int,
    figure_regions: dict[int, list[tuple[float, float, float, float]]] | None = None,
    page_furniture: furniture.Furniture | None = None,
    known_hyphens: set[str] | None = None,
    equation_regions: dict[int, list[tuple[float, float, float, float]]] | None = None,
) -> Reference:
    """Reference body text for a 1-indexed inclusive PDF page range.

    ``figure_regions`` maps page number to figure bounding boxes; supplying it
    removes figure-internal text (axis ticks, node labels) that would
    otherwise pollute both the coverage and numeric gates.

    ``equation_regions`` does the same for display equations, for a different
    reason: a formula's *rendered glyphs* -- subscripts fused onto their base
    letters, symbols with no ToUnicode mapping -- are not a byte-for-byte
    reading of the LaTeX that reproduces them, so a token-level diff between
    the two is not meaningful. Equations are verified independently instead:
    their count and labels against the structural gate, and their LaTeX
    against KaTeX strict mode in gate 5.
    """
    pages = list(range(first_page, last_page + 1))
    if page_furniture is None:
        import json as _json

        triage = _json.loads(config.TRIAGE_JSON.read_text())
        page_furniture = furniture.load_or_detect(doc, triage)

    sizes = [
        round(line.size, 1)
        for p in pages
        for line in _raw_rows(doc[p - 1])
    ]
    body_size = statistics.mode(sizes) if sizes else 12.0

    figure_regions = figure_regions or {}
    equation_regions = equation_regions or {}
    removed: list[RemovedLine] = []
    by_page: dict[int, str] = {}

    # Line-break hyphens must be resolved the same way the pipeline resolves
    # them. "social-" + "networking" is a real compound the book hyphenates,
    # not a word broken by TeX, and deciding that differently on each side of
    # the comparison produces a disagreement where the content is identical.
    # Whether a hyphen is kept depends on the form being attested elsewhere, so
    # the window this is collected over matters. Callers checking a single page
    # must pass the whole chapter's set, or a compound attested on another page
    # will be resolved differently here than by the pipeline.
    if known_hyphens is None:
        chapter_text = "\n".join(doc[p - 1].get_text() for p in pages)
        known_hyphens = textnorm.collect_hyphenated_forms(chapter_text)

    for page_no in pages:
        page = doc[page_no - 1]
        height = page.rect.height
        rects = [fitz.Rect(*b) for b in figure_regions.get(page_no, [])]
        eq_rects = [fitz.Rect(*b) for b in equation_regions.get(page_no, [])]
        kept: list[str] = []
        # A caption that runs several lines only carries its "Figure N.N:"
        # label on the first, so `_classify`'s exemption for that first line
        # does not by itself save the rest: a caption set inside an
        # oversized figure region (a wide table rendered as one image, with
        # its own caption text falling inside the same crop) has every
        # wrapped continuation line geometrically inside that same region,
        # and `_classify` alone would read them as figure interior and drop
        # them. Once a caption's opening line is recognised as sitting
        # inside a given figure rect, every following line still
        # substantially inside that *same* rect, set in that *same* font
        # size, is treated as more of that caption, mirroring how
        # `pipeline.stage2_reconcile` accumulates a caption block until it
        # leaves the extractor's own caption region -- but only immediately
        # below it, within one line-height's gap. A sub-caption ("(a) ...")
        # that annotates one panel in a tall stack of several sits inside
        # that whole figure's bounding box too, one ordinary line-height
        # above the *next* panel's own artwork resuming a line-height below
        # it -- its node labels, its own annotations set at their own
        # (smaller, or merely different) size, never the caption's -- so the
        # gap test alone cannot tell a wrapped second line of the same
        # caption apart from the next panel's content merely sitting close
        # beneath it. A genuine continuation is set in the exact same face
        # as its opener, so this checks that too, on the same tight
        # tolerance `pdfutil.reflow_grid_subcaptions` uses for its own
        # column continuations, not merely "close".
        # A caption opener that shared its baseline with a sibling opener
        # ("(a) ...", "(b) ...") must not seed its own continuation search
        # below: `pdfutil.reflow_grid_subcaptions` already looked for each
        # one's wrapped continuation among the unmerged lines, by column, and
        # a pair that shares this set is recognised by baseline here rather
        # than by re-matching the opener's text for a sibling, because
        # `reflow_grid_subcaptions` can leave the *other* member of the pair
        # as its own single-line `Line` -- indistinguishable by text alone
        # from an ordinary standalone sub-caption -- once either one needed
        # to consolidate a continuation of its own. Starting a search from
        # here anyway relitigates that question with a cruder test
        # (whole-figure-rect overlap instead of each opener's own x-column)
        # and can seize a panel's own axis or node labels sitting a line or
        # two below, at the same size, inside the same oversized figure rect.
        grid_openers = pdfutil.grid_opener_rows(list(pdfutil.iter_lines(page)), body_size)
        active_caption_rect: fitz.Rect | None = None
        caption_bottom = 0.0
        caption_size = 0.0
        for line in _rows(page, body_size, known_hyphens):
            rect = fitz.Rect(*line.bbox)
            if RE_CAPTION_START.match(line.text):
                active_caption_rect = None
                if round(line.y0, 1) not in grid_openers:
                    for fig in rects:
                        if fig.contains(rect) or (rect & fig).get_area() > 0.6 * rect.get_area():
                            active_caption_rect = fig
                            caption_bottom = line.y1
                            caption_size = line.size
                            break
                reason = None
            elif (
                active_caption_rect is not None
                and line.y0 - caption_bottom <= 20.0
                and abs(line.size - caption_size) <= 0.1
                and (rect & active_caption_rect).get_area() > 0.5 * rect.get_area()
            ):
                reason = page_furniture.reason_for(
                    line.text, line.y0, line.y1, line.size, body_size, height
                )
                if reason:
                    active_caption_rect = None
                else:
                    caption_bottom = line.y1
            else:
                active_caption_rect = None
                reason = _classify(line, height, page_furniture, body_size, rects, eq_rects)
            if reason:
                removed.append(RemovedLine(page_no, line.text, reason, line.bbox))
            elif kept:
                kept[-1] = textnorm.join_hyphenated(kept[-1], line.text, known_hyphens)
            else:
                kept.append(line.text)
        by_page[page_no] = "\n".join(kept)

    return Reference(
        pages=(first_page, last_page),
        body_text="\n".join(by_page[p] for p in pages),
        body_by_page=by_page,
        removed=removed,
    )


# --------------------------------------------------------------------------
# Independent structural counts, taken from the PDF rather than the model.
# --------------------------------------------------------------------------

RE_FIGURE_CAPTION = re.compile(r"^Figure\s+(\d+\.\d+)\s*[.:]", re.MULTILINE)
RE_TABLE_CAPTION = re.compile(r"^Table\s+(\d+\.\d+)\s*[.:]", re.MULTILINE)
RE_SECTION_HEAD = re.compile(r"^(\d+\.\d+)\s+\S")
RE_EXERCISE = re.compile(r"^\s*(\d{1,2})\.\s")


@dataclass
class StructuralCounts:
    figures: list[str]
    tables: list[str]
    sections: list[str]
    exercises: int
    footnotes: int
    equations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "figures": sorted(set(self.figures), key=_label_key),
            "tables": sorted(set(self.tables), key=_label_key),
            "sections": sorted(set(self.sections), key=_label_key),
            "exercises": self.exercises,
            "footnotes": self.footnotes,
            "equations": sorted(set(self.equations), key=_label_key),
        }


def _label_key(label: str) -> tuple[int, ...]:
    return tuple(int(p) for p in label.split("."))


def count_structures(
    doc: fitz.Document, first_page: int, last_page: int, chapter_number: int | None = None
) -> StructuralCounts:
    """Count labelled objects directly from the PDF text layer.

    Captions are matched at line start on the assembled line text, which is
    why this is independent of whatever the extractors decided a caption was.

    ``chapter_number``, when given, is also used to keep the equation count
    honest: unlike a figure or table caption, a trailing "(N.N)" has no
    distinguishing prefix of its own, so it collides with anything else a
    book prints in that exact shape at a line's end. A statistics-heavy
    chapter's regression tables print nothing else *but* that shape --
    coefficient after coefficient reported as "estimate (std. error)" -- and
    every one of those parenthesised standard errors reads as a plausible
    equation label with no way to tell it from a real one by pattern alone.
    A real equation's label is always "this chapter's number . its own
    index" (see equations.py); a standard error's is whatever two digits the
    statistic happens to round to, essentially never the current chapter's
    own number. Requiring the match makes the false positives (chapter 3
    alone had three: "(1.60)", "(2.93)", "(2.47)", none of them chapter 3's
    own) disappear without needing to special-case a single book, while a
    genuine equation -- always numbered for its own chapter -- is unaffected.
    """
    figures: list[str] = []
    tables: list[str] = []
    sections: list[str] = []
    equations: list[str] = []
    exercises = 0
    footnotes = 0

    sizes = [
        round(line.size, 1)
        for p in range(first_page, last_page + 1)
        for line in _raw_rows(doc[p - 1])
    ]
    body_size = statistics.mode(sizes) if sizes else 12.0

    in_exercises = False
    for page_no in range(first_page, last_page + 1):
        for line in _raw_rows(doc[page_no - 1]):
            text = line.text
            # Exercises are a numbered list that runs to the end of the
            # chapter. Requiring the marker to be the next integer in sequence
            # keeps prose such as "3. of the above" from inflating the count.
            if in_exercises:
                m = re.match(r"^(\d{1,2})\.\s+\S", text)
                if m and int(m.group(1)) == exercises + 1:
                    exercises += 1
            m = RE_FIGURE_CAPTION.match(text)
            if m:
                figures.append(m.group(1))
            m = RE_TABLE_CAPTION.match(text)
            if m:
                tables.append(m.group(1))
            # A numbered display equation's label, at the right margin of the
            # same visual row as the expression it numbers.
            m = RE_EQUATION_LABEL.search(text)
            if (
                m
                and (chapter_number is None or int(m.group(1)) == chapter_number)
                and not _looks_like_data_row(text)
            ):
                equations.append(f"{m.group(1)}.{m.group(2)}")
            # Section headings are set larger than body text.
            if line.size > body_size + 1.5:
                m = RE_SECTION_HEAD.match(text)
                if m:
                    sections.append(m.group(1))
                    if "Exercises" in text:
                        in_exercises = True
            # Footnote bodies are set a little smaller than body text and sit
            # at the foot of the page. Both conditions are needed: the karate
            # club figure's node labels are far smaller and sit high on the
            # page, and matching on size alone counted 16 of them as footnotes.
            rel_top = line.y0 / doc[page_no - 1].rect.height
            if (
                body_size - 3.5 <= line.size < body_size - 1.0
                and rel_top > 0.70
                and re.match(r"^\d{1,2}\s", text)
                and not textnorm.is_integer_soup(text)
            ):
                footnotes += 1

    return StructuralCounts(figures, tables, sections, exercises, footnotes, equations)
