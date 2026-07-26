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

from . import config, pdfutil

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7}

# Table-of-contents line shapes. Section entries are distinguished by their
# dotted leaders; chapter and part entries have none.
RE_PART = re.compile(r"^\s*(I|II|III|IV|V|VI|VII)\s\s+(\S.*?)\s{2,}(\d+)\s*$")
RE_CHAPTER = re.compile(r"^\s*(\d{1,2})\s+([A-Z]\S*.*?)\s{2,}(\d+)\s*$")
# The leader is usually many dots, but a title long enough to reach the page
# number column on its own (9.7's, at 75 characters) prints only " . "
# before it -- 3 characters, just under the 4+ this used to require, which
# silently dropped that section out of the table of contents. 3 consecutive
# dot/space characters still never occurs inside natural title prose, so
# lowering the threshold by one does not risk a false match mid-title.
RE_SECTION = re.compile(r"^\s*(\d{1,2}\.\d{1,2})\s*(\S.*?)[.\s]{3,}(\d+)\s*$")
RE_PREFACE = re.compile(r"^\s*(Preface)\s{2,}([ivxl]+)\s*$")

RE_CHAPTER_HEAD = re.compile(r"^Chapter\s*(\d{1,2})$")


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


def find_toc_pages(doc: fitz.Document, max_scan: int = 20) -> list[int]:
    """1-indexed PDF pages that make up the table of contents."""
    pages = []
    for i in range(min(max_scan, doc.page_count)):
        text = doc[i].get_text()
        head = text.strip().splitlines()[:1]
        if head and head[0].strip() == "Contents":
            pages.append(i + 1)
        elif pages and re.search(r"\bCONTENTS\b", text[:200]):
            pages.append(i + 1)
        elif pages:
            break
    return pages


def parse_toc(pdf: Path, toc_pages: list[int]) -> tuple[list[PartEntry], list[ChapterEntry], dict]:
    first, last = toc_pages[0], toc_pages[-1]
    text = run(["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(pdf), "-"])

    parts: list[PartEntry] = []
    chapters: list[ChapterEntry] = []
    front: dict = {}

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        m = RE_PREFACE.match(line)
        if m:
            front["preface_printed_page"] = m.group(2)
            continue

        m = RE_PART.match(line)
        if m:
            roman, title, page = m.group(1), m.group(2).strip(), int(m.group(3))
            parts.append(PartEntry(ROMAN[roman], roman, title, page))
            continue

        m = RE_SECTION.match(line)
        if m:
            number, title, page = m.group(1), m.group(2).strip(), int(m.group(3))
            chap_no = int(number.split(".")[0])
            for ch in chapters:
                if ch.number == chap_no:
                    ch.sections.append(SectionEntry(number, title, page))
                    break
            continue

        m = RE_CHAPTER.match(line)
        if m:
            number, title, page = int(m.group(1)), m.group(2).strip(), int(m.group(3))
            if title.upper() == "CONTENTS":
                continue
            chapters.append(ChapterEntry(number, title, page))
            continue

    return parts, chapters, front


def scan_chapter_starts(doc: fitz.Document) -> dict[int, int]:
    """Map chapter number -> 1-indexed PDF page, by finding the display heading.

    Chapter openers set 'Chapter N' at ~25pt; nothing else in the book does.
    """
    starts: dict[int, int] = {}
    for i in range(doc.page_count):
        for line in list(pdfutil.iter_lines(doc[i]))[:4]:
            if line.size <= 14:
                continue
            m = RE_CHAPTER_HEAD.match(line.text)
            if m:
                starts.setdefault(int(m.group(1)), i + 1)
    return starts


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
    starts = scan_chapter_starts(doc)

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

    biblio_page = find_heading_page(doc, "Bibliography", min_size=16.0)
    body_end = (biblio_page - 1) if biblio_page else doc.page_count

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

    # Spot-check: the mapped opener page must actually read "Chapter N".
    spot_checks = []
    for ch in ordered:
        text = doc[ch.pdf_page - 1].get_text()
        first = next((l.strip() for l in text.splitlines() if l.strip()), "")
        ok = first == f"Chapter {ch.number}"
        spot_checks.append(
            {"chapter": ch.number, "pdf_page": ch.pdf_page, "first_line": first, "ok": ok}
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
