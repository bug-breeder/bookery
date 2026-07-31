"""Stage 0 - acquire and triage.

Produces ``work/triage.json``: the structural facts about the PDF that every
later stage is keyed to. The most important product is the chapter/part
boundary map, which is derived from the table of contents and then verified
against the rendered pages rather than trusted.

Run:  python -m pipeline.stage0_acquire
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import fitz
from rapidfuzz import fuzz

from . import config, pdfutil, textnorm

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7}

# Table-of-contents line shapes. Section entries are distinguished by their
# dotted leaders; chapter and part entries have none. The chapter line's
# optional "Chapter " prefix and optional ":" accommodate both "3   Strong
# and Weak Ties   45" and "Chapter 3: Strong and Weak Ties   45" -- book
# design varies on this, the page-number column does not.
RE_PART = re.compile(r"^\s*(I|II|III|IV|V|VI|VII)\s\s+(\S.*?)\s{2,}(\d+)\s*$")
RE_CHAPTER = re.compile(r"^\s*(?:Chapter\s+)?(\d{1,2})\s*:?\s+([A-Z]\S*.*?)\s{2,}(\d+)\s*$")
# The leader is usually many dots, but a title long enough to reach the page
# number column on its own (9.7's, at 75 characters) prints only " . "
# before it -- 3 characters, just under the 4+ this used to require, which
# silently dropped that section out of the table of contents. 3 consecutive
# dot/space characters still never occurs inside natural title prose, so
# lowering the threshold by one does not risk a false match mid-title.
RE_SECTION = re.compile(r"^\s*(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\s*(\S.*?)[.\s]{3,}(\d+)\s*$")
RE_PREFACE = re.compile(r"^\s*(Preface)\s{2,}([ivxl]+)\s*$")

# A section/chapter title long enough to fill the whole printed line wraps
# onto a second (sometimes third) line, with the page number following
# whichever line the title text happens to end on. RE_SECTION/RE_CHAPTER
# only look at one physical line, so a wrapped entry's first line ("5.5 The
# Groucho Marx Theorem in Zero-Sum") has no trailing page number and fails
# to match, while its continuation line ("Betting  239") doesn't start with
# a number either -- the whole entry silently vanishes from the ToC. These
# two patterns identify a wrapped entry's opening line so its continuation
# can be joined back on before the real parsing loop ever sees it.
_RE_SECTION_START = re.compile(r"^\s*\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\s+\S")
_RE_CHAPTER_START = re.compile(r"^\s*(?:Chapter\s+)?\d{1,2}\s*:?\s+[A-Z]")
# The page number is always its own whitespace-delimited token, never a
# suffix glued onto the last title word -- requiring that boundary (rather
# than a bare `\d+|[ivxlcdm]+$`) matters because ordinary English words
# routinely end in letters that are individually valid roman numerals
# ("Iterated", "Betting", "Reasoning" all end in i/v/x/l/c/d/m), which would
# otherwise make nearly every wrapped title's first line look like it
# already carries a trailing page number and never get joined at all.
_RE_PAGE_TAIL = re.compile(r"(?:^|\s)(?:\d+|[ivxlcdm]+)\s*$", re.IGNORECASE)


def _join_wrapped_toc_lines(text: str) -> list[str]:
    """Re-join ToC entries whose title wrapped onto a following line.

    A complete entry line always ends in its page number (arabic for
    chapters/sections, roman for the preface); a wrapped title's first line
    does not, so it's buffered and appended to -- across as many
    continuation lines as needed -- until one carrying the page number
    shows up. Lines that don't look like the start of a section/chapter
    entry are passed through untouched, so this can't accidentally swallow
    unrelated ToC furniture (a lone "Contents" header, a part divider).
    """
    out: list[str] = []
    pending: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if pending is not None:
            pending = f"{pending} {stripped}"
            if _RE_PAGE_TAIL.search(stripped):
                out.append(pending)
                pending = None
            continue
        starts_entry = _RE_SECTION_START.match(line) or _RE_CHAPTER_START.match(line)
        if starts_entry and not _RE_PAGE_TAIL.search(stripped):
            pending = line
            continue
        out.append(line)
    if pending:
        out.append(pending)
    return out

# Headings that, if present, mark the end of chapter body content -- an
# index or appendix is not "Bibliography", but it still isn't chapter prose
# and the last chapter must not be allowed to swallow it. Order doesn't
# matter here; whichever is found earliest in the document wins.
BACK_MATTER_HEADINGS = ("Bibliography", "References", "Index", "Appendix")

# A fuzzy match ratio below this is treated as "not this chapter's opener",
# tolerating the punctuation/whitespace differences between how a title is
# typeset on the TOC page versus the chapter's own opening page. This has to
# be a strict *full-string* ratio, not a partial one: a short title like
# "Games" or "Contents" is a near-substring match for all sorts of unrelated
# headings once partial alignment is allowed, and a part-divider page's own
# title (e.g. "Game Theory") shares enough vocabulary with a chapter inside
# that part (e.g. "Evolutionary Game Theory") to false-positive too.
TITLE_MATCH_RATIO = 90.0

# A chapter-opener heading is usually the title alone, but is sometimes led
# by a number/roman-numeral ornament ("1", "Chapter 6", "III.") that isn't
# part of the title text used in the ToC. Stripped once from the front
# before matching, so "Chapter 6 Games" can still hit "Games" at a strict
# ratio without that prefix also being allowed to loosely match anything.
_LEADING_ORDINAL_RE = re.compile(
    r"^\s*(?:chapter\s+)?(?:\d{1,3}|[ivxlcdm]{1,6})\s*[:.\-\u2013\u2014]?\s+",
    re.IGNORECASE,
)


@dataclass
class SectionEntry:
    number: str
    title: str
    printed_page: int
    pdf_page: int | None = None


@dataclass
class ChapterEntry:
    number: int
    title: str
    printed_page: int
    pdf_page: int | None = None
    pdf_page_end: int | None = None
    part: int | None = None
    sections: list[SectionEntry] = field(default_factory=list)

    @property
    def id(self) -> str:
        return config.chapter_id(self.number)


@dataclass
class PartEntry:
    number: int
    roman: str
    title: str
    printed_page: int
    pdf_page: int | None = None
    chapters: list[int] = field(default_factory=list)

    @property
    def slug(self) -> str:
        words = re.sub(r"[^a-z0-9\s-]", "", self.title.lower()).split()
        return f"part{self.number}-" + "-".join(words)


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _title_from_filename(pdf: Path) -> str:
    return re.sub(r"[-_]+", " ", pdf.stem).strip().title()


def probe_info(pdf: Path) -> dict:
    out = run(["pdfinfo", str(pdf)])
    info: dict[str, str] = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip().lower().replace(" ", "_")] = v.strip()
    return info


def probe_fonts(pdf: Path) -> dict:
    out = run(["pdffonts", str(pdf)])
    rows = out.splitlines()[2:]
    total = embedded = with_unicode = 0
    families: set[str] = set()
    for row in rows:
        cols = row.split()
        if len(cols) < 7:
            continue
        total += 1
        name = cols[0]
        families.add(name.split("+")[-1])
        # Columns from the right are stable: object ID occupies the last two.
        emb, sub, uni = cols[-5], cols[-4], cols[-3]
        if emb == "yes":
            embedded += 1
        if uni == "yes":
            with_unicode += 1
    return {
        "total_fonts": total,
        "embedded": embedded,
        "all_embedded": total > 0 and embedded == total,
        "with_tounicode": with_unicode,
        # No ToUnicode CMap means glyph->character recovery is heuristic; this
        # is the root cause of split-accent and math-symbol artifacts.
        "tounicode_coverage": (with_unicode / total) if total else 0.0,
        "families": sorted(families),
    }


def probe_images(pdf: Path) -> dict[int, int]:
    out = run(["pdfimages", "-list", str(pdf)])
    counts: dict[int, int] = {}
    for line in out.splitlines()[2:]:
        cols = line.split()
        if not cols or not cols[0].isdigit():
            continue
        page = int(cols[0])
        counts[page] = counts.get(page, 0) + 1
    return counts


TOC_HEADING_RE = re.compile(r"^(?:table\s+of\s+)?contents$", re.IGNORECASE)


def find_toc_pages(doc: fitz.Document, max_scan: int = 50) -> list[int]:
    """1-indexed PDF pages that make up the table of contents.

    Some books print "Contents" as a running header on every ToC page;
    others print it once and every following ToC page's first line is just
    its own folio number, with "CONTENTS" appearing lower on the page
    instead. `max_scan` is generous (50) because a heavily-nested,
    multi-level ToC can run to 20+ pages on its own.
    """
    pages = []
    for i in range(min(max_scan, doc.page_count)):
        text = doc[i].get_text()
        head = text.strip().splitlines()[:1]
        if head and TOC_HEADING_RE.match(head[0].strip()):
            pages.append(i + 1)
        elif pages and re.search(r"\bCONTENTS\b", text[:200], re.IGNORECASE):
            pages.append(i + 1)
        elif pages:
            break
    return pages


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_toc_text(text: str) -> str:
    """Strip stray control characters before stripping whitespace.

    A dot-leader glyph with an incomplete ToUnicode mapping can decode to a
    control character (observed: a trailing "\\x08") instead of vanishing
    or becoming a normal dot/space -- plain `.strip()` leaves it in place
    since it isn't whitespace, and it then survives into every downstream
    title comparison.
    """
    return _CONTROL_CHARS_RE.sub("", text).strip()


def parse_toc(pdf: Path, toc_pages: list[int]) -> tuple[list[PartEntry], list[ChapterEntry], dict]:
    first, last = toc_pages[0], toc_pages[-1]
    text = run(["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(pdf), "-"])

    parts: list[PartEntry] = []
    chapters: list[ChapterEntry] = []
    front: dict = {}

    for line in _join_wrapped_toc_lines(text):
        m = RE_PREFACE.match(line)
        if m:
            front["preface_printed_page"] = m.group(2)
            continue

        m = RE_PART.match(line)
        if m:
            roman, title, page = m.group(1), _clean_toc_text(m.group(2)), int(m.group(3))
            parts.append(PartEntry(ROMAN[roman], roman, title, page))
            continue

        m = RE_SECTION.match(line)
        if m:
            number, title, page = m.group(1), _clean_toc_text(m.group(2)), int(m.group(3))
            chap_no = int(number.split(".")[0])
            for ch in chapters:
                if ch.number == chap_no:
                    ch.sections.append(SectionEntry(number, title, page))
                    break
            continue

        m = RE_CHAPTER.match(line)
        if m:
            number, title, page = int(m.group(1)), _clean_toc_text(m.group(2)), int(m.group(3))
            if title.upper() == "CONTENTS":
                continue
            chapters.append(ChapterEntry(number, title, page))
            continue

    return parts, chapters, front


def body_text_size(doc: fitz.Document, sample: int = 40) -> float:
    """Median line font size across a sample of pages.

    Used to size the "this is a heading, not body text" threshold relative
    to *this* book's own typography rather than a fixed point size --
    a 9pt-body technical book and an 11pt-body academic one both set
    headings well above their own body text, even though neither absolute
    size transfers to the other.
    """
    sizes: list[float] = []
    step = max(1, doc.page_count // sample)
    for i in range(0, doc.page_count, step):
        sizes.extend(line.size for line in pdfutil.iter_lines(doc[i]))
    sizes.sort()
    return sizes[len(sizes) // 2] if sizes else 10.0


def _heading_candidate(doc: fitz.Document, page_idx: int, threshold: float, max_lines: int = 8) -> str:
    """Text of every above-threshold line near the top of a page, joined.

    Joining matters because a chapter opener's heading is not always one
    line: some books set a bare oversized chapter number ("1") directly
    above the title ("Blockchain 101") as two separate large lines rather
    than one "Chapter 1" string.
    """
    lines = list(pdfutil.iter_lines(doc[page_idx]))[:max_lines]
    return " ".join(line.text for line in lines if line.size >= threshold)


def _title_matches(candidate: str, title: str, min_ratio: float = TITLE_MATCH_RATIO) -> bool:
    if not candidate.strip():
        return False
    # normalize() folds ligatures ("Eﬀects" -> "effects"), smart quotes, and
    # accent-repair artifacts that otherwise differ between the ToC's text
    # (usually pulled via pdftotext) and PyMuPDF's raw glyph-level extraction
    # of the heading on its own page.
    candidate_n = textnorm.normalize(candidate)
    title_n = textnorm.normalize(title)
    stripped_n = textnorm.normalize(_LEADING_ORDINAL_RE.sub("", candidate, count=1))
    best = max(fuzz.ratio(candidate_n, title_n), fuzz.ratio(stripped_n, title_n))
    return best >= min_ratio


def scan_chapter_starts(doc: fitz.Document, titles: dict[int, str]) -> tuple[dict[int, int], float]:
    """Map chapter number -> 1-indexed PDF page, by finding each chapter's
    own (already TOC-parsed) title set in a heading-sized font.

    Chapter-opener typography varies a lot between books: a literal
    "Chapter N" string, a bare oversized number, a title-only heading with
    no number at all. Matching against the chapter's own title -- which is
    known ahead of time from the table of contents -- rather than a guessed
    template is what makes this portable across book designs, since the
    title (not the wording around it) is the one thing guaranteed to be
    both unique and set large on its opening page.
    """
    body_size = body_text_size(doc)
    threshold = max(body_size * 1.5, body_size + 4)
    remaining = dict(titles)
    starts: dict[int, int] = {}
    for i in range(doc.page_count):
        if not remaining:
            break
        candidate = _heading_candidate(doc, i, threshold)
        if not candidate:
            continue
        for number, title in list(remaining.items()):
            if _title_matches(candidate, title):
                starts[number] = i + 1
                del remaining[number]
                break
    return starts, threshold


def find_heading_page(doc: fitz.Document, wanted: str, min_size: float = 14.0) -> int | None:
    for i in range(doc.page_count):
        for line in list(pdfutil.iter_lines(doc[i]))[:4]:
            if line.size > min_size and line.text == wanted:
                return i + 1
    return None


def build_triage(pdf: Path) -> dict:
    doc = fitz.open(pdf)
    info = probe_info(pdf)
    fonts = probe_fonts(pdf)
    images = probe_images(pdf)

    toc_pages = find_toc_pages(doc)
    if not toc_pages:
        raise SystemExit("FATAL: could not locate the table of contents")

    parts, chapters, front = parse_toc(pdf, toc_pages)
    titles = {ch.number: ch.title for ch in chapters}
    starts, heading_threshold = scan_chapter_starts(doc, titles)

    if len(chapters) != len(starts):
        raise SystemExit(
            f"FATAL: ToC lists {len(chapters)} chapters but {len(starts)} "
            f"chapter openers were found on the pages. Boundary map unreliable."
        )

    # The printed-folio to PDF-page offset must be constant across the body.
    offsets = set()
    for ch in chapters:
        pdf_page = starts.get(ch.number)
        if pdf_page is None:
            raise SystemExit(f"FATAL: no opener page found for chapter {ch.number}")
        ch.pdf_page = pdf_page
        offsets.add(pdf_page - ch.printed_page)

    if len(offsets) != 1:
        raise SystemExit(f"FATAL: inconsistent page offsets across chapters: {sorted(offsets)}")
    offset = offsets.pop()

    # Whichever back-matter heading (bibliography, index, appendix, ...)
    # appears earliest marks where chapter body content stops. Only
    # "Bibliography"/"References" gets its own emitted page -- see
    # stage4_emit and --skip-bibliography -- but any of them must stop the
    # last chapter from swallowing back matter it doesn't own.
    biblio_page = find_heading_page(doc, "Bibliography", min_size=16.0) or find_heading_page(
        doc, "References", min_size=16.0
    )
    back_matter_pages = [
        p
        for p in (biblio_page, *(find_heading_page(doc, h, min_size=16.0) for h in ("Index", "Appendix")))
        if p
    ]
    body_end = (min(back_matter_pages) - 1) if back_matter_pages else doc.page_count

    ordered = sorted(chapters, key=lambda c: c.number)
    for part in parts:
        part.pdf_page = part.printed_page + offset
    ordered_parts = sorted(parts, key=lambda p: p.number)
    divider_pages = {p.pdf_page for p in ordered_parts}

    for idx, ch in enumerate(ordered):
        nxt = ordered[idx + 1].pdf_page if idx + 1 < len(ordered) else None
        end = (nxt - 1) if nxt else body_end
        # A part divider sitting between two chapters belongs to the part, not
        # to the chapter that precedes it.
        for divider in sorted(divider_pages):
            if ch.pdf_page < divider <= end:
                end = divider - 1
                break
        ch.pdf_page_end = end
        for sec in ch.sections:
            sec.pdf_page = sec.printed_page + offset

    for idx, part in enumerate(ordered_parts):
        nxt_page = (
            ordered_parts[idx + 1].pdf_page
            if idx + 1 < len(ordered_parts)
            else body_end + 1
        )
        part.chapters = [
            c.number for c in ordered if part.pdf_page <= c.pdf_page < nxt_page
        ]
        for c in ordered:
            if c.number in part.chapters:
                c.part = part.number

    preface_page = find_heading_page(doc, "Preface", min_size=16.0)
    if preface_page is None:
        raise SystemExit("FATAL: could not locate the Preface heading")

    # The Preface is front matter, paginated in lowercase roman numerals
    # (i-vi) rather than the book's arabic body sequence, so it cannot share
    # the single `printed_to_pdf_offset` every numbered chapter is checked
    # against above -- that invariant is intentionally computed before this
    # point and never sees it. It is modelled as chapter 0 rather than a
    # bespoke parallel path so it gets the exact same stage1-4 and gate
    # machinery as every other chapter for free: `printed_page` here is a
    # 1-based position in its own roman sequence (i=1), not an arabic folio.
    preface_entry = ChapterEntry(
        number=0,
        title="Preface",
        printed_page=1,
        pdf_page=preface_page,
        pdf_page_end=ordered[0].pdf_page - 1,
        part=None,
        sections=[],
    )

    # Spot-check: the mapped opener page must actually carry this chapter's
    # own title in heading-sized text -- the same test `scan_chapter_starts`
    # used to find it, re-run here as an independent verification rather
    # than trusted as a side effect of the search.
    spot_checks = []
    for ch in ordered:
        heading_text = _heading_candidate(doc, ch.pdf_page - 1, heading_threshold)
        ok = _title_matches(heading_text, ch.title)
        spot_checks.append(
            {"chapter": ch.number, "pdf_page": ch.pdf_page, "heading_text": heading_text[:160], "ok": ok}
        )

    failed = [c for c in spot_checks if not c["ok"]]
    if failed:
        raise SystemExit(f"FATAL: boundary spot-check failed for {failed}")

    text_layer_pages = sum(1 for i in range(doc.page_count) if doc[i].get_text().strip())

    return {
        "source": {
            "path": str(pdf.relative_to(config.ROOT)),
            "sha256": run(["shasum", "-a", "256", str(pdf)]).split()[0],
            "bytes": pdf.stat().st_size,
            # Falls back to a title-cased filename when the PDF carries no
            # Title metadata, so the emitted site never hardcodes a book name.
            "title": info.get("title") or _title_from_filename(pdf),
            "author": info.get("author"),
        },
        "pdfinfo": info,
        "page_count": doc.page_count,
        "tagged": info.get("tagged") == "yes",
        "text_layer": {
            "present": text_layer_pages > 0,
            "pages_with_text": text_layer_pages,
            "coverage": round(text_layer_pages / doc.page_count, 4),
            "born_digital": "TeX" in info.get("creator", ""),
        },
        "fonts": fonts,
        "raster_images": {
            "total": sum(images.values()),
            "pages_with_images": len(images),
            "per_page": {str(k): v for k, v in sorted(images.items())},
        },
        "boundary_map": {
            "printed_to_pdf_offset": offset,
            "toc_pdf_pages": toc_pages,
            "preface_pdf_page": preface_page,
            "body_first_pdf_page": ordered[0].pdf_page,
            "body_last_pdf_page": body_end,
            "bibliography_pdf_page": biblio_page,
            "bibliography_last_pdf_page": doc.page_count if biblio_page else None,
            "parts": [
                {**asdict(p), "slug": p.slug} for p in ordered_parts
            ],
            "chapters": [
                {**asdict(preface_entry), "id": preface_entry.id},
                *({**asdict(c), "id": c.id} for c in ordered),
            ],
        },
        "spot_checks": spot_checks,
        "versions": config.tool_versions(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 0: acquire and triage a PDF")
    ap.add_argument("--pdf", type=Path, default=config.DEFAULT_PDF)
    ap.add_argument("--out", type=Path, default=config.TRIAGE_JSON)
    args = ap.parse_args()

    if not args.pdf.exists():
        raise SystemExit(f"FATAL: {args.pdf} not found. Run scripts/fetch_pdf.sh first.")

    config.ensure_dirs()
    triage = build_triage(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(triage, indent=2, ensure_ascii=False))

    bm = triage["boundary_map"]
    print(f"pages            : {triage['page_count']}")
    print(f"born digital     : {triage['text_layer']['born_digital']}")
    print(f"tounicode covg   : {triage['fonts']['tounicode_coverage']:.0%}")
    print(f"printed->pdf off : +{bm['printed_to_pdf_offset']}")
    print(f"parts / chapters : {len(bm['parts'])} / {len(bm['chapters'])}")
    print(f"bibliography     : pdf p{bm['bibliography_pdf_page']}-{bm['bibliography_last_pdf_page']}")
    print(f"spot-checks      : {sum(1 for c in triage['spot_checks'] if c['ok'])}/{len(triage['spot_checks'])} ok")
    print(f"wrote            : {args.out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
