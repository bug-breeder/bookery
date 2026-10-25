"""Ingest an EPUB into the canonical model.

    python -m pipeline.epub_ingest --epub BOOK.epub [--list] [--renumber] [--force]

An EPUB already *is* structured text, so there is nothing to extract or
reconcile: this reads the package (OPF), the spine and the table of contents
(EPUB 3 nav or EPUB 2 NCX), cuts the spine into chapters at the TOC's
top-level entries, and walks each chapter's XHTML in reading order into the
same block model the PDF pipeline writes. Downstream readers (`bookery learn`,
anything reading work/reconcile) then can't tell the two apart, except that
an EPUB's blocks only carry a page number when the book has print page
markers (epub:type="pagebreak").

Writes, under BOOKERY_ROOT:
  epub/book.epub              a copy of the source
  work/triage.json            source metadata and the chapter map
  work/reconcile/chNN.json    one canonical model per chapter
  work/assets.json            the figures, by chapter
  site/static/img/chNN/*.png  the figures' images, re-encoded as PNG

`--list` prints the chapter map and writes nothing; check it before the first
real run. Chapters keep the number the book gives them ("Chapter 3", "3.
Sorting"); a lone Introduction or Preface before chapter 1 becomes chapter 0
and an unnumbered Conclusion or Epilogue continues the sequence. `--renumber`
numbers every kept entry 1..N instead, for books whose TOC numbering is odd.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import html.entities
import io
import json
import posixpath
import re
import shutil
import sys
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import lxml.html
from lxml import etree
from PIL import Image

from . import config
from .model import Block, ChapterDoc

# ---------------------------------------------------------------------------
# Package: container -> OPF -> metadata, manifest, spine, TOC
# ---------------------------------------------------------------------------


@dataclass
class TocEntry:
    label: str
    doc: str                  # zip path of the target document
    frag: str | None          # fragment id within it, if any
    children: list["TocEntry"] = field(default_factory=list)


@dataclass
class Epub:
    path: Path
    zf: zipfile.ZipFile
    opf: str
    version: str
    title: str
    author: str
    language: str
    spine: list[str]          # zip paths of linear spine documents, in order
    toc: list[TocEntry]
    toc_kind: str             # "nav" | "ncx" | "spine"

    def read(self, name: str) -> bytes:
        return self.zf.read(name)


def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def _xml(data: bytes) -> etree._Element:
    parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
    return etree.fromstring(data, parser=parser)


def _join(base_doc: str, href: str) -> tuple[str, str | None]:
    href, _, frag = unquote(href).partition("#")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base_doc), href)) if href else base_doc
    return path, (frag or None)


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def open_epub(path: Path) -> Epub:
    zf = zipfile.ZipFile(path)
    container = _xml(zf.read("META-INF/container.xml"))
    rootfile = next(e for e in container.iter() if _local(e.tag) == "rootfile")
    opf_path = rootfile.get("full-path")
    opf = _xml(zf.read(opf_path))

    def meta(name: str) -> str:
        for e in opf.iter():
            if _local(e.tag) == name and _text(e):
                return _text(e)
        return ""

    authors = [_text(e) for e in opf.iter() if _local(e.tag) == "creator" and _text(e)]
    manifest: dict[str, dict] = {}
    for item in opf.iter():
        if _local(item.tag) == "item" and item.get("id") and item.get("href"):
            manifest[item.get("id")] = {
                "path": _join(opf_path, item.get("href"))[0],
                "type": item.get("media-type") or "",
                "props": (item.get("properties") or "").split(),
            }
    spine: list[str] = []
    ncx_id = None
    for e in opf.iter():
        if _local(e.tag) == "spine":
            ncx_id = e.get("toc")
        if _local(e.tag) == "itemref" and e.get("idref") in manifest and e.get("linear", "yes") != "no":
            spine.append(manifest[e.get("idref")]["path"])

    toc: list[TocEntry] = []
    kind = "spine"
    nav = next((m for m in manifest.values() if "nav" in m["props"]), None)
    if nav is not None:
        toc, kind = _read_nav(zf, nav["path"]), "nav"
    if not toc:
        ncx = manifest.get(ncx_id) if ncx_id else None
        ncx = ncx or next((m for m in manifest.values() if m["type"] == "application/x-dtbncx+xml"), None)
        if ncx is not None:
            toc, kind = _read_ncx(zf, ncx["path"]), "ncx"
    if not toc:
        toc = [TocEntry(label=posixpath.basename(d), doc=d, frag=None) for d in spine]
        kind = "spine"
    return Epub(
        path=path, zf=zf, opf=opf_path, version=opf.get("version") or "",
        title=meta("title"), author="; ".join(authors), language=meta("language"),
        spine=spine, toc=toc, toc_kind=kind,
    )


def _read_nav(zf: zipfile.ZipFile, nav_path: str) -> list[TocEntry]:
    root = _xml(zf.read(nav_path))
    navs = [e for e in root.iter() if _local(e.tag) == "nav"]
    toc_nav = next((n for n in navs if "toc" in _epub_type(n)), navs[0] if navs else None)
    if toc_nav is None:
        return []
    ol = next((e for e in toc_nav if _local(e.tag) == "ol"), None)

    def walk(ol_el) -> list[TocEntry]:
        out = []
        for li in ol_el:
            if _local(li.tag) != "li":
                continue
            a = next((e for e in li.iter() if _local(e.tag) in ("a", "span")), None)
            if a is None:
                continue
            sub = next((e for e in li if _local(e.tag) == "ol"), None)
            href = a.get("href")
            children = walk(sub) if sub is not None else []
            if href:
                doc, frag = _join(nav_path, href)
                out.append(TocEntry(_text(a), doc, frag, children))
            elif children:
                # A heading-only entry (no link) starts where its first child does.
                out.append(TocEntry(_text(a), children[0].doc, children[0].frag, children))
        return out

    return walk(ol) if ol is not None else []


def _read_ncx(zf: zipfile.ZipFile, ncx_path: str) -> list[TocEntry]:
    root = _xml(zf.read(ncx_path))
    nav_map = next((e for e in root.iter() if _local(e.tag) == "navmap"), None)

    def walk(parent) -> list[TocEntry]:
        out = []
        for p in parent:
            if _local(p.tag) != "navpoint":
                continue
            label = next((e for e in p.iter() if _local(e.tag) == "text"), None)
            content = next((e for e in p if _local(e.tag) == "content"), None)
            if content is None or not content.get("src"):
                continue
            doc, frag = _join(ncx_path, content.get("src"))
            out.append(TocEntry(_text(label), doc, frag, walk(p)))
        return out

    return walk(nav_map) if nav_map is not None else []


def _epub_type(el) -> list[str]:
    for k, v in el.attrib.items():
        if _local(k) == "type" and ("epub" in k or "ops" in k):
            return v.split()
    return []


# ---------------------------------------------------------------------------
# Chapter map
# ---------------------------------------------------------------------------

RE_SKIP = re.compile(
    r"^(cover|front ?cover|back ?cover|cover[- ]?page|title( page)?|half[- ]title( page)?|series( page)?|"
    r"copyright( page)?|dedication|epigraph|contents|table of contents|toc|landmarks|mục lục|"
    r"list of (figures|tables|illustrations)|also by .*|other books.*|by the same author|"
    r"praise( for .*)?|about the authors?|(a )?note (about|on) the authors?|about the publisher|"
    r"acknowledge?ments?|((author|subject|name) )?index|index of .*|searchable terms|notes|"
    r"end ?notes|bibliography|references|works cited|further reading|glossary.*|credits|"
    r"permissions|front ?matter|back ?matter|newsletters?.*|sign up.*|advertisement|"
    r"begin reading|start reading|page[ _-]?[0-9ivxlc]+)$",
    re.IGNORECASE,
)
RE_PAGE_ENTRY = re.compile(r"^page[ _-]?[0-9ivxlc]+$", re.IGNORECASE)
RE_OPENER = re.compile(r"^(preface|foreword|prologue|introduct\w*)\b", re.IGNORECASE)
_NUM = r"([0-9]+|[ivxlc]+|[a-z]+)"
RE_PART = re.compile(rf"^(?:part|section|book|unit|phần)\s+{_NUM}\b\s*[.:\-–—|]?\s*", re.IGNORECASE)
# "PHẦN MỘT - 1. Title": a part prefix in front of the chapter's own number.
RE_PART_PREFIX = re.compile(r"^(?:part|section|book|phần)\s+\S+\s*[-–—:|]\s*(?=\d)", re.IGNORECASE)
RE_CHAPTER_NUM = re.compile(
    rf"^(?:chapter|chap\.?|ch\.?|chương)\s+{_NUM}\b\s*[.:\-–—|]?\s*"
    r"|^([0-9]+)(?![.,]\d)(?=[\s.:\-–—|])\s*[.:\-–—|]?\s*",
    re.IGNORECASE,
)
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
# Explicit chapter numbers only count when they read as a sequence: each one
# a little above the last. "20 Percent Time" in a book of unnumbered chapters
# is a title, not chapter 20.
MAX_NUMBER_STEP = 5


def _roman(s: str) -> int | None:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    s = s.lower()
    if not s or any(c not in vals for c in s):
        return None
    total = 0
    for i, c in enumerate(s):
        v = vals[c]
        total += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return total


def _to_int(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    return NUMBER_WORDS.get(word.lower()) or _roman(word)


def _split_number(label: str) -> tuple[int | None, str]:
    text = label.strip()
    text = text[RE_PART_PREFIX.match(text).end():] if RE_PART_PREFIX.match(text) else text
    m = RE_CHAPTER_NUM.match(text)
    if not m:
        return None, label
    n = _to_int(m.group(1) or m.group(2))
    if n is None:
        return None, label
    return n, text[m.end():].strip() or label


def _title(label: str, accepted: bool) -> str:
    """The label without its chapter number: always for a number read as one,
    otherwise only when the label says so ("Chapter 3", "3.", "3:"), since
    "20 Percent Time" is a title that happens to start with a number."""
    n, title = _split_number(label)
    if n is None:
        return label.strip()
    text = label.strip()
    text = text[RE_PART_PREFIX.match(text).end():] if RE_PART_PREFIX.match(text) else text
    m = RE_CHAPTER_NUM.match(text)
    explicit = m.group(1) is not None or bool(re.match(r"\d+\s*[.:\-–—|]", text))
    return title if accepted or explicit else label.strip()


@dataclass
class ChapterPlan:
    number: int
    title: str
    label: str                # the TOC entry as written
    part: int | None
    entry: TocEntry


@dataclass
class _Item:
    entry: TocEntry
    part: int | None
    skip: str | None = None   # why it isn't a chapter; None means it is one


def _with_children(e: TocEntry, extra: list[TocEntry]) -> TocEntry:
    return TocEntry(e.label, e.doc, e.frag, e.children + extra)


def _mostly_numbered(entries: list[TocEntry]) -> bool:
    return len(entries) >= 2 and 2 * sum(_split_number(c.label)[0] is not None for c in entries) >= len(entries)


def _holds_chapters(e: TocEntry) -> bool:
    """A grouping entry (a part, under whatever name) rather than a chapter
    with sections: its children are mostly numbered and live in their own
    files, where a chapter's sections share the chapter's file."""
    return (_split_number(e.label)[0] is None and _mostly_numbered(e.children)
            and len({c.doc for c in e.children}) >= 2)


def _fold_unnumbered(entries: list[TocEntry]) -> list[TocEntry]:
    """In a run of numbered chapters, an unnumbered entry after one of them is
    one of its sections ("1. Just Start", "Begin Before You Are Ready", "2. ...")."""
    if not _mostly_numbered(entries):
        return entries
    out: list[TocEntry] = []
    for e in entries:
        if out and _split_number(e.label)[0] is None and not RE_SKIP.match(e.label.strip()):
            out[-1] = _with_children(out[-1], [e])
        else:
            out.append(e)
    return out


def _toc_items(toc: list[TocEntry]) -> list[_Item]:
    """The TOC entries that can bound a chapter, with parts opened up."""
    items: list[_Item] = []
    part: int | None = None
    for i, e in enumerate(toc):
        label = e.label.strip()
        pm = RE_PART.match(label)
        if RE_SKIP.match(label):
            items.append(_Item(e, None, "front or back matter"))
            continue
        if e.children and (pm or _holds_chapters(e)):
            if pm:
                part = _to_int(pm.group(1))
            items.append(_Item(e, part, "part: its chapters are listed under it"))
            for c in _fold_unnumbered(e.children):
                items.append(_Item(c, part, "front or back matter" if RE_SKIP.match(c.label.strip()) else None))
            if pm:
                part = None
            continue
        if pm:
            part = _to_int(pm.group(1))
            nxt = toc[i + 1] if i + 1 < len(toc) else None
            if nxt is not None and (nxt.doc, nxt.frag) == (e.doc, e.frag):
                items.append(_Item(e, part, "part divider"))
                continue
        prev = items[-1] if items else None
        if prev is not None and prev.skip is None and e.frag and prev.entry.frag is None and prev.entry.doc == e.doc:
            # A flat TOC that lists sections beside their chapter: an entry
            # pointing inside the document the previous chapter starts.
            prev.entry = _with_children(prev.entry, [e])
            continue
        items.append(_Item(e, part))
    return items


def plan_chapters(epub: Epub, renumber: bool = False) -> tuple[list[ChapterPlan], list[dict], list[TocEntry], list[str]]:
    """Chapters to ingest, entries skipped (with why), every boundary entry, and notes."""
    items = _toc_items(epub.toc)
    kept = [it for it in items if it.skip is None]
    parsed = [_split_number(it.entry.label) for it in kept]
    notes: list[str] = []

    accepted: dict[int, int] = {}            # kept index -> chapter number
    prev = None
    for i, (n, _) in enumerate(parsed):
        if n is None:
            continue
        if prev is not None and n <= prev:
            # Numbering starts over (several books in one, or numbered
            # appendices): from here on chapters continue the sequence.
            notes.append(f"numbering restarts at {kept[i].entry.label.strip()!r}; "
                         "chapters from there on continue after the last numbered one")
            break
        if prev is None or n <= prev + MAX_NUMBER_STEP:
            accepted[i] = prev = n
    sequential = renumber or len(accepted) < 2 or 10 * len(accepted) < 3 * len(kept)

    plans: list[ChapterPlan] = []
    if sequential:
        for i, it in enumerate(kept):
            # The book's numbers weren't trusted, so none counts as read.
            plans.append(ChapterPlan(i + 1, _title(it.entry.label, False), it.entry.label, it.part, it.entry))
    else:
        first, last = min(accepted), max(accepted)
        openers = [i for i in range(first) if RE_OPENER.match(kept[i].entry.label.strip())]
        zero = openers[-1] if openers and 0 not in accepted.values() else None
        number = max(accepted.values())
        for i, it in enumerate(kept):
            label = it.entry.label.strip()
            if i in accepted:
                plans.append(ChapterPlan(accepted[i], _title(label, True), it.entry.label, it.part, it.entry))
            elif i < first:
                if i == zero:
                    plans.append(ChapterPlan(0, label, it.entry.label, it.part, it.entry))
                else:
                    it.skip = "unnumbered entry before chapter 1 (--renumber keeps it)"
            elif i < last:
                # Between numbered chapters: a section of the one before it.
                it.skip = "folded into the chapter before it"
                plans[-1].entry = _with_children(plans[-1].entry, [it.entry])
            else:
                number += 1
                plans.append(ChapterPlan(number, _title(label, False), it.entry.label, it.part, it.entry))
    skipped = [{"label": it.entry.label, "reason": it.skip} for it in items
               if it.skip and it.skip != "part: its chapters are listed under it"]
    boundaries = [it.entry for it in items if it.skip != "folded into the chapter before it"]
    return plans, skipped, boundaries, notes


# ---------------------------------------------------------------------------
# XHTML documents
# ---------------------------------------------------------------------------

RE_XML_DECL = re.compile(rb"^\s*<\?xml[^>]*\?>", re.S)
RE_ENCODING = re.compile(rb"""encoding\s*=\s*["']([A-Za-z0-9_\-]+)["']""")
RE_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.I)


RE_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _parse_xhtml(text: str):
    """Parse a content document as XML when it is well-formed XHTML (EPUB
    requires it), and as HTML only when it is not. The HTML parser reads a
    self-closing <span epub:type="pagebreak"/> as an open span that swallows
    the rest of the page."""
    if not text.strip():
        return lxml.html.document_fromstring("<html><body></body></html>")
    # XHTML written with HTML's named entities (&nbsp;, &mdash;) and no DTD.
    xml_text = RE_ENTITY.sub(
        lambda m: m.group(0) if m.group(1) in XML_ENTITIES or m.group(1) not in html.entities.name2codepoint
        else f"&#{html.entities.name2codepoint[m.group(1)]};",
        text,
    )
    try:
        parser = etree.XMLParser(resolve_entities=False, huge_tree=True, no_network=True)
        return etree.fromstring(xml_text.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError:
        return lxml.html.document_fromstring(text)


class Doc:
    """One spine document: parsed tree, document order, ids."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        m = RE_ENCODING.search(data[:200]) if data.lstrip().startswith(b"<?xml") else None
        m = m or RE_META_CHARSET.search(data[:2000])
        encoding = m.group(1).decode() if m else "utf-8"
        try:
            text = RE_XML_DECL.sub(b"", data).decode(encoding)
        except (LookupError, UnicodeDecodeError):
            text = RE_XML_DECL.sub(b"", data).decode("utf-8", errors="replace")
        self.root = _parse_xhtml(text)
        self.order: dict = {}
        self.ids: dict[str, object] = {}
        for i, el in enumerate(self.root.iter()):
            self.order[el] = i
            if isinstance(el.tag, str):
                for key in ("id", "name"):
                    v = el.get(key)
                    if v and v not in self.ids:
                        self.ids[v] = el
        body = next((e for e in self.root.iter() if _local(e.tag) == "body"), None)
        self.body = body if body is not None else self.root

    def index_of(self, frag: str | None) -> int:
        if not frag or frag not in self.ids:
            return 0
        return self.order[self.ids[frag]]

    def last_index(self, el) -> int:
        return self.order[deque(el.iter(), maxlen=1)[0]]

    def find_heading(self, label: str) -> int | None:
        """Document-order index of the heading that reads as `label`, if any."""
        if not hasattr(self, "_headings"):
            self._headings = [(self.order[e], _norm(text_of(e))) for e in self.body.iter()
                              if _local(e.tag) in HEADINGS]
        want = _norm(label)
        if not want:
            return None
        for idx, text in self._headings:
            if text == want:
                return idx
        for idx, text in self._headings:
            if text and (text.startswith(want) or want.startswith(text)) and min(len(text), len(want)) >= 8:
                return idx
        return None


HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKISH = HEADINGS | {
    "p", "div", "ul", "ol", "dl", "table", "figure", "pre", "blockquote", "section",
    "article", "aside", "header", "footer", "main", "nav", "hr", "figcaption", "math", "svg",
}
SKIP_TAGS = {"script", "style", "head", "title", "nav", "noscript", "template"}
NOTE_TYPES = {"footnote", "endnote", "rearnote", "footnotes", "endnotes", "rearnotes", "note"}
RE_NOTE_CLASS = re.compile(r"^(fn|fnote|foot-?note.*|end-?note.*|note|notes|ntx|fntext)$", re.I)
RE_CAPTION_CLASS = re.compile(r"(^cap\d*$|caption|^figcap|^fig-?cap)", re.I)
RE_EQUATION_CLASS = re.compile(r"(equation|formula|displaymath|display-math|^math$|^eq$)", re.I)
# Heading classes in the wild: p.H1 / p.AH / p.chapterHeadA for sections,
# p.CT / h2.chapterTitle / p.chapterSubtitle for the chapter's own title.
RE_TITLE_CLASS = re.compile(r"^(ct|cn|cst|ctitle|title|subtitle|(chapter|chap|part)[-_]?(sub)?(title|head|heading|number|num|no)\d*)$", re.I)
SECTION_CLASS_LEVELS = [
    (re.compile(r"^(h1|hd1|head1|heading1|ah|ahd|ahead|a-head|head-?a|heading-?a|subhead1?|sec(tion)?-?(title|head)|.*head(ing)?-?a)$", re.I), 2),
    (re.compile(r"^(h2|hd2|head2|heading2|bh|bhd|bhead|b-head|head-?b|heading-?b|subhead2|.*head(ing)?-?b)$", re.I), 3),
    (re.compile(r"^(h3|hd3|head3|heading3|ch|chd|chead|c-head|head-?c|heading-?c|subhead3|.*head(ing)?-?c)$", re.I), 4),
]


def _classes(el) -> list[str]:
    return (el.get("class") or "").split()


def _is_pagebreak(el) -> bool:
    if not isinstance(el.tag, str):
        return False
    return "pagebreak" in _epub_type(el) or el.get("role") == "doc-pagebreak"


def _page_value(el) -> int | None:
    for v in (el.get("title"), el.get("aria-label"), _text(el), el.get("id")):
        if v:
            m = re.search(r"(\d+)\s*$", v.strip())
            if m:
                return int(m.group(1))
    return None


def _tex(s: str) -> str:
    """TeX as the book stores it, without $ delimiters or a second layer of escaping."""
    s = html.unescape(s.strip())
    return re.sub(r"^\$\$?\s*|\s*\$\$?$|^\\[(\[]\s*|\s*\\[)\]]$", "", s).strip()


def _math_tex(el) -> str:
    alt = el.get("alttext")
    if alt:
        return _tex(alt)
    for a in el.iter():
        if _local(a.tag) == "annotation" and "tex" in (a.get("encoding") or "").lower():
            return _tex(_text(a))
    return _text(el)


RE_NOTEREF_CLASS = re.compile(r"^(fn|fnref|noteref|note-?ref|foot-?note.*|end-?note.*|superscript-?link)$", re.I)
RE_NOTEREF_TEXT = re.compile(r"^\[?[0-9]{1,3}[a-z]?\]?$|^[*†‡§¶]+$", re.I)


def _is_noteref(e) -> bool:
    """A note marker in running text (<sup><a href="#n1">1</a></sup>,
    epub:type="noteref"). The note itself is kept as its own block; the
    marker would only glue a digit to the word before it ("lines1")."""
    if not isinstance(e.tag, str):
        return False
    if "noteref" in _epub_type(e) or e.get("role") == "doc-noteref":
        return True
    tag = _local(e.tag)
    link = e if tag == "a" else None
    if tag == "sup" and len(e) == 1 and _local(e[0].tag) == "a" and not (e.text or "").strip():
        link = e[0]
    if link is None or "#" not in (link.get("href") or "") or link.get("href", "").startswith("http"):
        return False
    if not RE_NOTEREF_TEXT.match(_text(link)):
        return False
    return tag == "sup" or any(RE_NOTEREF_CLASS.match(c) for c in _classes(link))


def _looks_like_tex(s: str | None) -> bool:
    return bool(s) and (s.strip().startswith("$") or "\\" in s)


def _collect(e, parts: list[str], top: bool) -> None:
    tag = _local(e.tag)
    if not top and (tag in ("script", "style") or _is_pagebreak(e) or not tag or _is_noteref(e)):
        pass
    elif tag == "math":
        parts.append(" $" + _math_tex(e) + "$ ")
    elif tag == "img" and not top and _looks_like_tex(e.get("alt")):
        # An inline equation set as a picture, with its TeX as alt text.
        parts.append(" $" + _tex(e.get("alt")) + "$ ")
    elif tag == "br":
        parts.append(" ")
    else:
        # Block boundaries separate words even when the markup has no
        # whitespace between them (<span>Fig. 4.1</span><p>Perfect…</p>).
        block = not top and (tag in BLOCKISH or tag in ("li", "td", "th", "dt", "dd", "tr"))
        if block:
            parts.append(" ")
        if e.text:
            parts.append(e.text)
        for c in e:
            _collect(c, parts, False)
        if block:
            parts.append(" ")
    if not top and e.tail:
        parts.append(e.tail)


def text_of(el) -> str:
    """Readable text of an element: page markers and scripts dropped, MathML as its TeX."""
    parts: list[str] = []
    _collect(el, parts, True)
    return " ".join("".join(parts).split())


@dataclass
class _Run:
    """Inline content between the block children of a mixed container:
    <div>text <span>$x$</span> more <div class="Equation">…</div> tail</div>."""
    parent: object
    nodes: list               # the inline elements in the run
    after: object | None      # the block child the run follows, if any
    text: str


def _leaves(el, note: bool = False):
    """Block-level leaves under `el` in reading order: (kind, element, in_note)."""
    tag = _local(el.tag)
    if not tag or tag in SKIP_TAGS or _is_pagebreak(el):
        return
    note = note or bool(NOTE_TYPES & set(_epub_type(el))) or el.get("role") in ("doc-footnote", "doc-endnote", "doc-endnotes")
    if tag in HEADINGS:
        yield ("heading", el, note)
    elif tag == "p":
        yield ("p", el, note)
    elif tag in ("ul", "ol", "dl"):
        yield ("list", el, note)
    elif tag == "table":
        yield ("table", el, note)
    elif tag == "figure":
        yield ("figure", el, note)
    elif tag == "pre":
        yield ("code", el, note)
    elif tag in ("img", "svg", "image"):
        yield ("img", el, note)
    elif tag == "math":
        yield ("math", el, note)
    elif tag == "hr":
        return
    elif any(RE_EQUATION_CLASS.search(c) for c in _classes(el)) and any(_local(x.tag) in ("img", "math") for x in el.iter()):
        yield ("equation", el, note)
    elif any(_local(c.tag) in BLOCKISH for c in el):
        parts, nodes, after = [el.text or ""], [], None
        for c in el:
            if _local(c.tag) in BLOCKISH and not _is_pagebreak(c):
                text = " ".join("".join(parts).split())
                if text:
                    yield ("run", _Run(el, nodes, after, text), note)
                yield from _leaves(c, note)
                parts, nodes, after = [c.tail or ""], [], c
            else:
                _collect(c, parts, False)
                nodes.append(c)
        text = " ".join("".join(parts).split())
        if text:
            yield ("run", _Run(el, nodes, after, text), note)
    elif text_of(el):
        yield ("p", el, note)
    else:
        for img in el.iter():
            if _local(img.tag) in ("img", "svg"):
                yield ("img", img, note)
                break


# ---------------------------------------------------------------------------
# Chapter extraction
# ---------------------------------------------------------------------------

RE_FIG_LABEL = re.compile(
    r"^[^\w(]*(fig(?:ure)?\.?|table|tbl\.?|exhibit|chart|plate|map|diagram|illustration|listing|algorithm)\s*"
    r"([0-9]+(?:[.\-–][0-9]+)*(?:[a-z](?![a-z]))?)\s*[.:\-–—|]?\s*",
    re.IGNORECASE,
)
RE_SECTION_LABEL = re.compile(r"^[^\w(]*(\d+(?:\.\d+)+)\.?\s+")
RE_ITEM_NUMBER = re.compile(r"^\(?[0-9a-z]{1,3}[.)]\s*", re.I)
RE_EQ_LABEL = re.compile(r"\((\d+(?:\.\d+)*)\)\s*$")
MIN_FIGURE_PX = 48
# Converters fill alt with placeholders; they describe nothing.
RE_GENERIC_ALT = re.compile(r"^\s*(image|img|picture|photo|figure|fig|graphic|illustration|cover|[\w\-]+\.(jpe?g|png|gif|svg|webp))?\s*\d*\s*$", re.I)


@dataclass
class Raw:
    kind: str                 # heading | paragraph | caption | footnote | list | table | figure | equation | code
    text: str = ""
    level: int | None = None
    level_hint: str | None = None   # "title" | "section" | "tag"
    page: int | None = None
    img: str | None = None    # zip path of the image
    alt: str = ""
    caption: str = ""
    cells: list[list[str]] | None = None
    latex: str | None = None
    ids: set[str] = field(default_factory=set)
    classes: list[str] = field(default_factory=list)
    doc: str = ""             # spine document it came from
    end: int = -1             # document-order index of its last element
    toc: bool = False         # a heading the TOC names as a section
    own_level: int | None = None   # its level in its own document, before the TOC re-levelled it


def _img_src(doc: Doc, el) -> str | None:
    for k, v in el.attrib.items():
        if _local(k) in ("src", "href") and v and not v.startswith("data:"):
            return _join(doc.name, v)[0]
    return None


def _first_img(el):
    for e in el.iter():
        if _local(e.tag) in ("img", "image"):
            return e
    return None


def _raw_from_leaf(doc: Doc, kind: str, el, note: bool) -> list[Raw]:
    if kind == "run":
        ids = {e.get("id") for n in el.nodes for e in n.iter() if isinstance(e.tag, str) and e.get("id")}
        return [Raw("footnote" if note else "paragraph", el.text, ids=ids)]
    ids = {e.get("id") for e in el.iter() if isinstance(e.tag, str) and e.get("id")}
    classes = _classes(el)
    if kind == "heading":
        return [Raw("heading", text_of(el), level=int(_local(el.tag)[1]), level_hint="tag", ids=ids, classes=classes)]
    if kind == "p":
        text = text_of(el)
        if not text:
            img = _first_img(el)
            if img is not None and kind == "p":
                return [Raw("figure", img=_img_src(doc, img), alt=img.get("alt") or "", ids=ids, classes=classes)]
            return []
        if note or any(RE_NOTE_CLASS.match(c) for c in classes):
            return [Raw("footnote", text, ids=ids, classes=classes)]
        if any(RE_CAPTION_CLASS.search(c) for c in classes):
            return [Raw("caption", text, ids=ids, classes=classes)]
        words = len(text.split())
        if words <= 20 and not text.endswith((".", ",", ";")):
            for c in classes:
                if RE_TITLE_CLASS.match(c):
                    return [Raw("heading", text, level=1, level_hint="title", ids=ids, classes=classes)]
                for rx, level in SECTION_CLASS_LEVELS:
                    if rx.match(c):
                        return [Raw("heading", text, level=level, level_hint="section", ids=ids, classes=classes)]
        return [Raw("footnote" if note else "paragraph", text, ids=ids, classes=classes)]
    if kind == "list":
        items, n = [], 0
        for li in el:
            if _local(li.tag) in ("li", "dt", "dd"):
                n += 1
                t = text_of(li)
                if t:
                    if _local(el.tag) != "ol":
                        t = f"- {t}"
                    elif not RE_ITEM_NUMBER.match(t):   # some books print the number themselves
                        t = f"{n}. {t}"
                    items.append(t)
        if not items:
            return []
        return [Raw("footnote" if note else "list", "\n".join(items), ids=ids, classes=classes)]
    if kind == "table":
        rows = []
        for tr in el.iter():
            if _local(tr.tag) == "tr":
                row = [text_of(c) for c in tr if _local(c.tag) in ("td", "th")]
                if any(row):
                    rows.append(row)
        cap = next((c for c in el if _local(c.tag) == "caption"), None)
        return [Raw("table", cells=rows, caption=text_of(cap) if cap is not None else "", ids=ids, classes=classes)]
    if kind == "figure":
        img = _first_img(el)
        cap = next((c for c in el.iter() if _local(c.tag) == "figcaption"), None)
        if img is None:
            # A <figure> holding a table or text: keep its content, caption first.
            out = []
            for k2, e2, n2 in _leaves_children(el, note):
                out += _raw_from_leaf(doc, k2, e2, n2)
            if cap is not None and out and out[0].kind == "table" and not out[0].caption:
                out[0].caption = text_of(cap)
            return out
        return [Raw("figure", img=_img_src(doc, img), alt=img.get("alt") or "",
                    caption=text_of(cap) if cap is not None else "", ids=ids, classes=classes)]
    if kind == "img":
        return [Raw("figure", img=_img_src(doc, el), alt=el.get("alt") or "", ids=ids, classes=classes)]
    if kind == "code":
        return [Raw("code", "".join(el.itertext()).strip("\n"), ids=ids, classes=classes)]
    if kind in ("math", "equation"):
        math = el if _local(el.tag) == "math" else next((m for m in el.iter() if _local(m.tag) == "math"), None)
        img = _first_img(el)
        latex = _math_tex(math) if math is not None else (_tex(img.get("alt") or "") if img is not None else None)
        if latex and RE_GENERIC_ALT.match(latex):
            latex = None
        text = text_of(el)
        raw = Raw("equation", "" if RE_GENERIC_ALT.match(text) else text, latex=latex or None, ids=ids, classes=classes)
        if img is not None and not raw.latex and not raw.text:
            raw.img = _img_src(doc, img)     # an equation set as a picture, and nothing else
        return [raw]
    return []


def _leaves_children(el, note: bool):
    for c in el:
        if _local(c.tag) != "figcaption":
            yield from _leaves(c, note)


def _doc_raws(doc: Doc, start: int, end: int | None) -> list[Raw]:
    """Raw blocks of `doc` whose leaf starts at or after `start` (or contains it) and ends before `end`."""
    marks = sorted((doc.order[e], _page_value(e)) for e in doc.root.iter() if _is_pagebreak(e))
    out: list[Raw] = []
    for kind, el, note in _leaves(doc.body):
        if kind == "run":
            first = (doc.order[el.nodes[0]] if el.nodes else
                     doc.last_index(el.after) + 1 if el.after is not None else doc.order[el.parent])
            last = doc.last_index(el.nodes[-1]) if el.nodes else first
        else:
            first, last = doc.order[el], doc.last_index(el)
        if last < start:
            continue
        if end is not None and (first >= end or last >= end):
            break
        page = None
        for idx, value in marks:
            if idx > first + 1:
                break
            if value is not None:
                page = value
        for r in _raw_from_leaf(doc, kind, el, note):
            r.page, r.doc, r.end = page, doc.name, last
            out.append(r)
    return out


def _bind_captions(raws: list[Raw]) -> list[Raw]:
    """Attach caption paragraphs to the figure or table next to them."""
    out: list[Raw] = []
    used: set[int] = set()

    def is_caption(r: Raw) -> bool:
        return r.kind == "caption" or (
            r.kind == "paragraph" and RE_FIG_LABEL.match(r.text) is not None and len(r.text.split()) <= 80
        )

    for i, r in enumerate(raws):
        if i in used:
            continue
        if r.kind in ("figure", "table") and not r.caption:
            if i + 1 < len(raws) and is_caption(raws[i + 1]):
                r.caption = raws[i + 1].text
                used.add(i + 1)
            elif out and is_caption(out[-1]) and (i - 1) not in used:
                r.caption = out.pop().text
        out.append(r)
    # A caption that found no exhibit is still the book's text.
    for r in out:
        if r.kind == "caption":
            r.kind = "paragraph"
    return out


def _nest_under_toc(raws: list[Raw]) -> None:
    """A heading the TOC doesn't name, after one it does, is part of that
    section. Books split into one file per TOC entry restart at <h1> in every
    file, so a sub-chapter's own <h2>s would otherwise land beside it instead
    of under it: keep each such heading's depth below the TOC heading it
    follows (how much deeper its tag is, or one level when that says nothing)."""
    anchor: tuple[int, int | None] | None = None
    for r in raws:
        if r.kind != "heading":
            continue
        if r.toc:
            anchor = (r.level or 2, r.own_level)
        elif anchor is not None and r.level is not None:
            level, own = anchor
            step = r.level - own if own is not None and r.level > own else 1
            r.level = min(6, max(r.level, level + step))


def _settle_headings(raws: list[Raw], chapter_title: str) -> None:
    """Headings before the first body paragraph (or the first TOC section)
    are the chapter's opener (level 1); every later heading sits at level 2
    or deeper, so sections never share the chapter title's level."""
    seen_body = False
    for r in raws:
        if r.kind in ("paragraph", "list", "table", "figure") and len(r.text.split()) > 25:
            seen_body = True
        if r.toc:
            seen_body = True
        if r.kind != "heading" or r.toc:
            continue
        if not seen_body:
            r.level = 1
        elif (r.level or 1) <= 1:
            r.level = 2
    if not any(r.kind == "heading" and r.level == 1 for r in raws) and chapter_title:
        raws.insert(0, Raw("heading", chapter_title, level=1, level_hint="toc"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _apply_toc_sections(raws: list[Raw], entry: TocEntry, docs: dict[str, "Doc"], level: int = 2) -> None:
    """TOC entries nested under the chapter are its sections. The first block
    at or after each one's target becomes a heading at its depth: a heading
    already, or a paragraph that reads as the entry's label. When the book
    never prints the label, it goes in as a heading of its own."""
    for child in entry.children:
        doc = docs.get(child.doc)
        if doc is not None:
            at = doc.index_of(child.frag)
            i = next((i for i, r in enumerate(raws) if r.doc == child.doc and r.end >= at), None)
            if i is not None:
                r = raws[i]
                if r.kind == "heading" or (r.kind == "paragraph" and _norm(r.text) == _norm(child.label)):
                    r.own_level = r.level if r.kind == "heading" else None
                    r.kind, r.level, r.toc = "heading", max(level, r.level or level), True
                else:
                    raws.insert(i, Raw("heading", child.label.strip(), level=level, level_hint="toc",
                                       page=r.page, doc=r.doc, end=r.end, toc=True))
        _apply_toc_sections(raws, child, docs, min(level + 1, 6))


def _is_rule(im: Image.Image) -> bool:
    """A horizontal rule or section-break flourish set as an image: all of
    its ink sits in one thin band. (An equation image is just as wide and
    short, but its glyphs fill its height.)"""
    gray = im.convert("RGBA")
    flat = Image.new("RGBA", gray.size, (255, 255, 255, 255))
    flat.alpha_composite(gray)
    ink = flat.convert("L").point(lambda v: 255 if v < 160 else 0).getbbox()
    if ink is None:
        return True     # blank
    w, h = ink[2] - ink[0], ink[3] - ink[1]
    return h <= max(12, im.height // 5) and w >= 6 * h


def _export_image(epub: Epub, src: str, out: Path, keep_rules: bool = True) -> tuple[int, int] | None:
    if src.lower().endswith(".svg"):
        return None
    try:
        data = epub.read(src)
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if min(im.size) < MIN_FIGURE_PX:
                return None
            if not keep_rules and _is_rule(im):
                return None
            if im.mode not in ("RGB", "RGBA", "L", "LA"):
                im = im.convert("RGBA" if "transparency" in im.info or im.mode in ("P", "PA") else "RGB")
            out.parent.mkdir(parents=True, exist_ok=True)
            im.save(out, "PNG", compress_level=3)
            return im.size
    except (KeyError, OSError, ValueError):
        return None


def extract_chapter(epub: Epub, plan: ChapterPlan, boundaries: list[tuple[int, int]], docs: dict[str, Doc],
                    ornaments: frozenset[str] = frozenset()) -> tuple[ChapterDoc, list[dict]]:
    def doc(name: str) -> Doc:
        if name not in docs:
            docs[name] = Doc(name, epub.read(name))
        return docs[name]

    start = _position(epub, plan.entry, doc)
    after = [b for b in boundaries if b > start]
    end = min(after) if after else (len(epub.spine), 0)

    raws: list[Raw] = []
    for si in range(start[0], min(end[0], len(epub.spine) - 1) + 1):
        if si == end[0] and end[1] == 0:
            break
        d = doc(epub.spine[si])
        lo = start[1] if si == start[0] else 0
        hi = end[1] if si == end[0] else None
        raws += _doc_raws(d, lo, hi)

    raws = _bind_captions(raws)
    _apply_toc_sections(raws, plan.entry, docs)
    _nest_under_toc(raws)
    _settle_headings(raws, plan.title)

    cid = config.chapter_id(plan.number)
    img_dir = config.STATIC_IMG / cid
    blocks: list[Block] = []
    assets: list[dict] = []
    for r in raws:
        bid = f"{cid}-b{len(blocks) + 1:04d}"
        b = Block(id=bid, type=r.kind, page=r.page or 0, text=r.text, source="epub")
        if r.kind == "heading":
            b.level = r.level
            m = RE_SECTION_LABEL.match(r.text)
            if m:
                b.label = m.group(1)
        elif r.kind == "equation":
            b.latex = r.latex
            if r.img:
                b.flags.append("equation_image")
            m = RE_EQ_LABEL.search(r.text)
            if m:
                b.label = m.group(1)
        elif r.kind in ("figure", "table"):
            caption = r.caption
            m = RE_FIG_LABEL.match(caption) if caption else None
            if m:
                b.label = re.sub(r"[\-–]", ".", m.group(2))
                b.label_kind = "table" if m.group(1).lower().startswith("t") else "figure"
                caption = caption[m.end():].strip()
            elif r.kind == "table":
                b.label_kind = "table"
            b.caption = caption or None
            b.alt = (r.alt if not RE_GENERIC_ALT.match(r.alt) else "") or caption or None
            if r.kind == "table":
                b.cells = r.cells
            elif r.img in ornaments and not caption:
                continue
            else:
                stem = (f"{'tbl' if b.label_kind == 'table' else 'fig'}-{b.label.replace('.', '-')}"
                        if b.label else bid.rsplit("-", 1)[-1])
                size = _export_image(epub, r.img, img_dir / f"{stem}.png", keep_rules=bool(caption)) if r.img else None
                if size is None:
                    if not caption:
                        # An ornament, a glyph, or an image we can't decode, with nothing
                        # to say about it: not a figure.
                        continue
                    b.flags.append("image_not_extracted")
                else:
                    b.anchor = stem
                    b.asset = f"img/{cid}/{stem}.png"
                    file = img_dir / f"{stem}.png"
                    assets.append({
                        "label": b.label, "label_kind": b.label_kind, "block": bid,
                        "file": b.asset, "width": size[0], "height": size[1],
                        "page": r.page, "printed_page": r.page, "alt": b.alt,
                        "caption": b.caption, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                        "source": "epub", "href": r.img,
                    })
        blocks.append(b)

    pages = [b.page for b in blocks if b.page]
    span = (min(pages), max(pages)) if pages else (0, 0)
    chapter = ChapterDoc(chapter=plan.number, title=plan.title, part=plan.part,
                         pages=span, printed_pages=span, blocks=blocks)
    return chapter, assets


ORNAMENT_USES = 3


def _ornaments(epub: Epub, doc) -> frozenset[str]:
    """Images the book uses over and over (a quote mark on every blockquote, a
    section-break flourish): design, not figures. Uncaptioned ones are dropped."""
    uses: dict[str, int] = {}
    for name in epub.spine:
        try:
            d = doc(name)
        except KeyError:
            continue
        for el in d.root.iter():
            if isinstance(el.tag, str) and _local(el.tag) in ("img", "image"):
                src = _img_src(d, el)
                if src:
                    uses[src] = uses.get(src, 0) + 1
    return frozenset(src for src, n in uses.items() if n >= ORNAMENT_USES)


def _position(epub: Epub, entry: TocEntry, doc) -> tuple[int, int]:
    """(spine index, document-order index) where a TOC entry points."""
    if entry.doc not in epub.spine:
        return (-1, -1)
    si = epub.spine.index(entry.doc)
    if entry.frag and entry.frag not in doc(entry.doc).ids:
        # Converters that split long files (calibre's *_split_NNN) can leave
        # the TOC pointing at the first part while the anchor moved on.
        order = list(range(si + 1, len(epub.spine))) + list(range(si))
        found = next((sj for sj in order if entry.frag in doc(epub.spine[sj]).ids), None)
        if found is not None:
            si = found
        else:
            # Or dropped it altogether: fall back to the heading that reads
            # as the entry's label.
            for sj in [si] + order:
                at = doc(epub.spine[sj]).find_heading(entry.label)
                if at is not None:
                    return (sj, at)
    return (si, doc(epub.spine[si]).index_of(entry.frag))


def _trailing_matter(epub: Epub, after: tuple[int, int], doc) -> tuple[int, str] | None:
    """The first spine document past the last TOC entry that names itself back
    matter (its <title> or first heading reads "About the Author") though the
    TOC leaves it out; without a boundary there the last chapter runs on
    through it."""
    for si in range(after[0] + 1, len(epub.spine)):
        try:
            d = doc(epub.spine[si])
        except KeyError:
            continue
        title = next((e for e in d.root.iter() if isinstance(e.tag, str) and _local(e.tag) == "title"), None)
        head = next((e for e in d.body.iter() if isinstance(e.tag, str) and _local(e.tag) in HEADINGS), None)
        for el in (title, head):
            label = " ".join(text_of(el).split()) if el is not None else ""
            if label and RE_SKIP.match(label):
                return si, label
    return None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def ingest(epub_path: Path, renumber: bool = False, force: bool = False, list_only: bool = False) -> int:
    epub = open_epub(epub_path)
    plans, skipped, boundary_entries, notes = plan_chapters(epub, renumber)
    docs: dict[str, Doc] = {}

    def doc(name: str) -> Doc:
        if name not in docs:
            docs[name] = Doc(name, epub.read(name))
        return docs[name]

    boundaries = sorted({p for p in (_position(epub, e, doc) for e in boundary_entries) if p[0] >= 0})
    trailing = _trailing_matter(epub, boundaries[-1], doc) if boundaries else None
    if trailing:
        boundaries.append((trailing[0], 0))
        skipped.append({"label": trailing[1], "reason": "back matter the table of contents leaves out"})

    print(f"{epub.title or epub_path.name} — {epub.author or 'unknown author'} "
          f"(EPUB {epub.version}, TOC from {epub.toc_kind}, {len(epub.spine)} spine documents)")
    for pl in plans:
        part = f"part {pl.part}  " if pl.part else ""
        print(f"  ch{pl.number:02d}  {part}{pl.title}   [{pl.entry.doc}{'#' + pl.entry.frag if pl.entry.frag else ''}]")
    for s in skipped:
        print(f"  skip  {s['label']}  ({s['reason']})")
    for note in notes:
        print(f"  note: {note}")
    if not plans:
        why = "no chapters found in the table of contents"
        if skipped and all(RE_PAGE_ENTRY.match(s["label"]) for s in skipped if s["label"].lower() != "cover"):
            why += (" -- it lists pages, not chapters (a page-per-file conversion with no chapter "
                    "structure); use the book's PDF instead")
        print(why, file=sys.stderr)
        return 1
    if list_only:
        return 0

    triage_path = config.TRIAGE_JSON
    if triage_path.exists() and not force:
        existing = json.loads(triage_path.read_text())
        if existing.get("source", {}).get("format") != "epub":
            print(f"{triage_path} belongs to a PDF book; refusing to overwrite it (use --force, "
                  "or point BOOKERY_ROOT at a new folder for this EPUB)", file=sys.stderr)
            return 1

    config.ensure_dirs()
    dest = config.ROOT / "epub" / "book.epub"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if epub_path.resolve() != dest.resolve():
        shutil.copyfile(epub_path, dest)
    data = dest.read_bytes()

    ornaments = _ornaments(epub, doc)
    chapters_meta: list[dict] = []
    all_assets: dict[str, list[dict]] = {}
    starts: dict[tuple[int, int], int] = {}
    for pl in plans:
        at = _position(epub, pl.entry, doc)
        if at[0] < 0:
            skipped.append({"label": pl.label, "reason": "TOC target is not in the spine"})
            continue
        if at in starts:
            skipped.append({"label": pl.label, "reason": f"starts where chapter {starts[at]} starts (its anchor is missing)"})
            continue
        starts[at] = pl.number
        cid = config.chapter_id(pl.number)
        shutil.rmtree(config.STATIC_IMG / cid, ignore_errors=True)
        chapter, assets = extract_chapter(epub, pl, boundaries, docs, ornaments)
        out = config.RECONCILE_DIR / f"{cid}.json"
        out.write_text(json.dumps(chapter.as_dict(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        all_assets[cid] = assets
        first, last = chapter.pages
        sections = [
            {"number": b.label or "", "title": b.text, "printed_page": b.page or None, "pdf_page": b.page or None}
            for b in chapter.blocks if b.type == "heading" and (b.level or 1) > 1
        ]
        chapters_meta.append({
            "number": pl.number, "id": cid, "title": pl.title, "part": pl.part,
            # EPUBs have no PDF pages: when the book carries print page
            # markers both fields hold printed pages (an offset of zero),
            # otherwise both are null and lessons cite blocks without pages.
            "printed_page": first or None, "pdf_page": first or None, "pdf_page_end": last or None,
            "sections": sections, "href": pl.entry.doc + (f"#{pl.entry.frag}" if pl.entry.frag else ""),
        })
        n_fig = sum(1 for b in chapter.blocks if b.type == "figure")
        n_words = sum(len(b.text.split()) for b in chapter.blocks if b.type in ("paragraph", "list", "footnote"))
        print(f"  wrote {cid}: {len(chapter.blocks)} blocks, {n_words:,} words, "
              f"{len(sections)} headings, {n_fig} figures ({len(assets)} images)")

    triage = {
        "source": {
            "path": str(dest.relative_to(config.ROOT)), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "title": epub.title, "author": epub.author,
            "language": epub.language, "format": "epub",
        },
        "epub": {
            "version": epub.version, "opf": epub.opf, "toc": epub.toc_kind,
            "spine": len(epub.spine),
            "page_markers": any(c["printed_page"] for c in chapters_meta),
        },
        "boundary_map": {"chapters": chapters_meta, "skipped": skipped},
        "versions": config.tool_versions(),
    }
    triage_path.write_text(json.dumps(triage, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    config.ASSETS_JSON.write_text(json.dumps(all_assets, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"triage: {triage_path}")
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--epub", type=Path, required=True)
    ap.add_argument("--list", action="store_true", help="print the chapter map and write nothing")
    ap.add_argument("--renumber", action="store_true", help="number kept TOC entries 1..N")
    ap.add_argument("--force", action="store_true", help="overwrite a PDF book's triage.json")
    args = ap.parse_args(argv)
    if not args.epub.exists():
        raise SystemExit(f"{args.epub} does not exist")
    sys.exit(ingest(args.epub, renumber=args.renumber, force=args.force, list_only=args.list))


if __name__ == "__main__":
    main()
