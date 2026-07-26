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
from dataclasses import dataclass, field

import fitz

from . import config, pdfutil, textnorm

TOP_ZONE = 0.085
BOTTOM_ZONE = 0.90
LOWER_ZONE = 0.75
MIN_REPEATS = 3

_RE_DIGITS = re.compile(r"\d+")
RE_BARE_FOLIO = re.compile(r"^(\d{1,4}|[ivxlcdm]{1,7})$", re.IGNORECASE)


def mask(text: str) -> str:
    """Digit-masked key, so folio-bearing headers group together."""
    return _RE_DIGITS.sub("#", textnorm.normalize(text))


@dataclass
class Furniture:
    header_slot: bool = True
    folio_slot: bool = False
    boilerplate_keys: set[str] = field(default_factory=set)

    def reason_for(
        self, text: str, y0: float, y1: float, size: float, body_size: float, height: float
    ) -> str | None:
        """Why this line is furniture, or None if it is content."""
        rel_top = y0 / height
        if rel_top < TOP_ZONE and self.header_slot:
            return "running_header"
        if rel_top > BOTTOM_ZONE and (self.folio_slot or RE_BARE_FOLIO.match(text)):
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
            "header_slot": self.header_slot,
            "folio_slot": self.folio_slot,
            "boilerplate_keys": sorted(self.boilerplate_keys),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Furniture":
        return cls(
            header_slot=data["header_slot"],
            folio_slot=data["folio_slot"],
            boilerplate_keys=set(data["boilerplate_keys"]),
        )


def detect(doc: fitz.Document, pages: list[int]) -> Furniture:
    """Scan pages for the header slot, folio slot, and repeated footers.

    The per-chapter citation footer appears once per chapter, so it only
    clears the recurrence threshold when the scan spans the whole book. That
    is why callers pass every body page rather than a single chapter.
    """
    header_pages: set[int] = set()
    folio_pages: set[int] = set()
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
        for row in rows:
            sizes.append(round(row.size, 1))
            rel_top = row.y0 / height
            if rel_top < TOP_ZONE:
                header_pages.add(page_no)
            if rel_top > BOTTOM_ZONE:
                folio_pages.add(page_no)
            if rel_top > LOWER_ZONE:
                lower_hits.setdefault(mask(row.text), set()).add(page_no)

    import statistics

    body_size = statistics.mode(sizes) if sizes else 12.0
    denominator = considered or 1

    boilerplate = {
        key
        for key, hit_pages in lower_hits.items()
        if len(hit_pages) >= MIN_REPEATS and not RE_BARE_FOLIO.match(key)
    }

    return Furniture(
        header_slot=len(header_pages) / denominator >= 0.5,
        folio_slot=len(folio_pages) / denominator >= 0.5,
        boilerplate_keys=boilerplate,
    )


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
    import statistics

    sizes = [
        round(row.size, 1)
        for page in range(first, last + 1)
        for row in pdfutil.page_lines(doc, page)
    ]
    return statistics.mode(sizes) if sizes else 12.0
