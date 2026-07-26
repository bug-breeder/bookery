"""Page furniture detection, shared by reconciliation and verification.

Both stages must agree on what counts as furniture. If the emitter strips a
line that the reference builder keeps, the coverage gate reports a content
loss that never happened -- and if the reverse, a real loss hides. One
implementation, used by both, removes that whole class of false signal.

Detection is positional and recurrence-based rather than keyed to this
book's strings, so it carries over to another PDF.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field

import fitz

from . import config, pdfutil, textnorm

# Broad nets used only to gather *candidates*; the zone actually enforced
# against every page is measured from what's typically found within these
# nets (see `detect`), not assumed as a fixed fraction of page height. A
# book's margins can be tighter or looser than another's, and neither
# extreme is knowable in advance.
CANDIDATE_TOP_ZONE = 0.22
CANDIDATE_BOTTOM_ZONE = 0.78
LOWER_ZONE = 0.75
MIN_REPEATS = 3

# A vertical gap bigger than this (as a fraction of page height) ends a
# "furniture cluster" grown from the page edge inward. Consecutive lines
# within one paragraph sit almost flush against each other (a gap under
# ~0.002H); the whitespace a book reserves to visually separate its running
# head from the body column is several times that even on a tightly margined
# page, so a small threshold still cleanly separates the two without also
# needing to be anywhere near as large as the gap before a subheading or
# (especially) a chapter's own oversized opening heading -- which must not
# be allowed to inflate the zone height applied to every *other* page.
CLUSTER_GAP = 0.01

# Padding added past the measured header/folio extent so a line-height's
# worth of jitter between pages doesn't clip the boundary line itself.
ZONE_PAD = 0.01

# A line set at a size well beyond anything used for an in-body heading is
# a decorative display element -- a chapter's giant opening numeral or
# title, a part divider's title -- regardless of where on the page it sits
# or how many other pages happen to carry one just like it. This is what
# keeps a one-line-per-chapter opener out of the body/reference text even
# though it recurs far too rarely (once every twenty-odd pages) to clear
# the recurrence bar the positional header zone requires.
DISPLAY_HEADING_RATIO = 2.2
DISPLAY_HEADING_MIN = 20.0

# A chapter-opener heading ("Chapter 1" / "Overview") that is too modest to
# clear `display_heading_threshold` still isn't ordinary body text -- it is
# `pipeline.stage2_reconcile`'s level-1 heading, which `stage4_emit` never
# prints (the synthetic "N. Title" line already carries it). A book whose
# opener happens to sit below the display-heading cutoff must still have
# that heading excluded here the same way, or the reference ends up crediting
# words and digits from a heading the emitted document represents exactly
# once, via the frontmatter title, rather than zero or two times.
CHAPTER_OPENER_MARGIN = 8.0

_RE_DIGITS = re.compile(r"\d+")
RE_BARE_FOLIO = re.compile(r"^(\d{1,4}|[ivxlcdm]{1,7})$", re.IGNORECASE)


def mask(text: str) -> str:
    """Digit-masked key, so folio-bearing headers group together."""
    return _RE_DIGITS.sub("#", textnorm.normalize(text))


def display_heading_threshold(body_size: float) -> float:
    return max(body_size * DISPLAY_HEADING_RATIO, DISPLAY_HEADING_MIN)


def is_chapter_opener_size(size: float, body_size: float) -> bool:
    """Same "too large to be an ordinary heading" test stage2 uses.

    Kept distinct from `display_heading_threshold`: a display heading is
    furniture wherever it appears, while this only matters for the one line
    (or two, when a numeral and a title are set separately) that stage2
    would classify as the chapter's own level-1 heading and stage4 skips.
    """
    return size > body_size + CHAPTER_OPENER_MARGIN


@dataclass
class Furniture:
    header_zone: float = 0.0
    folio_zone: float = 1.0
    boilerplate_keys: set[str] = field(default_factory=set)

    def reason_for(
        self, text: str, y0: float, y1: float, size: float, body_size: float, height: float
    ) -> str | None:
        """Why this line is furniture, or None if it is content."""
        if size >= display_heading_threshold(body_size):
            return "display_heading"
        rel_top = y0 / height
        if self.header_zone and rel_top < self.header_zone:
            return "running_header"
        if rel_top > self.folio_zone and RE_BARE_FOLIO.match(text.strip()):
            return "page_folio"
        if (
            rel_top > LOWER_ZONE
            and size < body_size - 0.5
            and mask(text) in self.boilerplate_keys
        ):
            return "footer_boilerplate"
        return None

    def as_dict(self) -> dict:
        return {
            "header_zone": self.header_zone,
            "folio_zone": self.folio_zone,
            "boilerplate_keys": sorted(self.boilerplate_keys),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Furniture":
        return cls(
            header_zone=data["header_zone"],
            folio_zone=data["folio_zone"],
            boilerplate_keys=set(data["boilerplate_keys"]),
        )


def _edge_cluster_extent(rows: list[tuple[float, float]], from_top: bool) -> float | None:
    """Grow a cluster from the page edge inward and return its far extent.

    ``rows`` are (rel_top, rel_bottom) pairs already restricted to one
    candidate zone. Growing from the edge rather than looking at the zone as
    a whole is what keeps a page's *own* running head -- one or two lines
    that hug the edge -- separate from something unrelated that happens to
    also fall inside the broad candidate net (a display heading sitting
    well below the head, on a chapter's opening page).
    """
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r[0], reverse=from_top is False)
    extent = ordered[0][1] if from_top else ordered[0][0]
    for top, bottom in ordered[1:]:
        gap = (top - extent) if from_top else (extent - bottom)
        if gap > CLUSTER_GAP:
            break
        extent = max(extent, bottom) if from_top else min(extent, top)
    return extent


def detect(doc: fitz.Document, pages: list[int]) -> Furniture:
    """Scan pages for the header zone, folio zone, and repeated footers.

    The header zone height is the *typical* (median) extent of the
    top-edge-hugging cluster on each page, not a fixed fraction of page
    height: a book with tight margins needs a shorter zone than one with
    generous margins, and using the actual measurement is what lets one
    detector serve both. The median (rather than the max) is what keeps a
    rare outlier -- a chapter's oversized opening heading landing inside the
    same broad candidate net on its one page out of twenty-odd -- from
    inflating the zone applied to every ordinary page.

    The folio zone is found differently: unlike a header, the bottom of a
    page's body text is not at a consistent height book to book, or even
    page to page -- it depends on wherever a paragraph happens to end -- so
    "the edge-hugging cluster's typical extent" would just describe ordinary
    prose on any book without a running footer. A folio is instead
    recognised by *what* it is (a bare page number, `RE_BARE_FOLIO`)
    recurring near the bottom across most pages; the zone is then measured
    from where that recognised content actually sits.

    The per-chapter citation footer appears once per chapter, so it only
    clears the recurrence threshold when the scan spans the whole book. That
    is why callers pass every body page rather than a single chapter.
    """
    top_extents: list[float] = []
    folio_tops: list[float] = []
    lower_hits: dict[str, set[int]] = {}
    sizes: list[float] = []
    considered = 0

    for page_no in pages:
        page = doc[page_no - 1]
        height = page.rect.height
        rows = pdfutil.page_lines(doc, page_no)
        if not rows:
            continue
        considered += 1
        top_zone_rows: list[tuple[float, float]] = []
        page_folio_top: float | None = None
        for row in rows:
            sizes.append(round(row.size, 1))
            rel_top = row.y0 / height
            rel_bottom = row.y1 / height
            if rel_top < CANDIDATE_TOP_ZONE:
                top_zone_rows.append((rel_top, rel_bottom))
            if rel_bottom > CANDIDATE_BOTTOM_ZONE and RE_BARE_FOLIO.match(row.text.strip()):
                page_folio_top = rel_top if page_folio_top is None else min(page_folio_top, rel_top)
            if rel_top > LOWER_ZONE:
                lower_hits.setdefault(mask(row.text), set()).add(page_no)

        top_ext = _edge_cluster_extent(top_zone_rows, from_top=True)
        if top_ext is not None:
            top_extents.append(top_ext)
        if page_folio_top is not None:
            folio_tops.append(page_folio_top)

    body_size = statistics.mode(sizes) if sizes else 12.0
    denominator = considered or 1

    # A zone only exists if most pages actually carry something in it --
    # otherwise this book has no running head (or no folio) at all, and the
    # measurement would just be describing sparse, unrelated content.
    header_zone = (
        statistics.median(top_extents) + ZONE_PAD
        if len(top_extents) / denominator >= 0.5
        else 0.0
    )
    # Unlike the header, a folio does not need to show up on most pages to
    # be real: a book can print its page number in the *header* row on
    # every ordinary page (already covered by the header-zone/RE_BARE_FOLIO
    # combination above) and only fall back to a bottom-of-page folio on a
    # chapter's first page, where the header would otherwise collide with
    # the opening heading -- a few percent of pages, nowhere near a
    # majority. A handful of confirmed sightings is enough; `RE_BARE_FOLIO`
    # is what keeps this from ever matching an ordinary paragraph.
    folio_zone = min(folio_tops) - ZONE_PAD if len(folio_tops) >= MIN_REPEATS else 1.0

    boilerplate = {
        key
        for key, hit_pages in lower_hits.items()
        if len(hit_pages) >= MIN_REPEATS and not RE_BARE_FOLIO.match(key)
    }

    return Furniture(header_zone=header_zone, folio_zone=folio_zone, boilerplate_keys=boilerplate)


def load_or_detect(doc: fitz.Document, triage: dict) -> Furniture:
    """Book-wide furniture, computed once and cached to work/."""
    cache = config.WORK / "furniture.json"
    if cache.exists():
        return Furniture.from_dict(json.loads(cache.read_text()))

    bm = triage["boundary_map"]
    pages = list(range(bm["body_first_pdf_page"], bm["body_last_pdf_page"] + 1))
    result = detect(doc, pages)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result.as_dict(), indent=1, ensure_ascii=False))
    return result


def body_size_for(doc: fitz.Document, first: int, last: int) -> float:
    sizes = [
        round(row.size, 1)
        for page in range(first, last + 1)
        for row in pdfutil.page_lines(doc, page)
    ]
    return statistics.mode(sizes) if sizes else 12.0
