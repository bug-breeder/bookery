"""EPUB ingest: chapter mapping, block walking, captions, pages, outputs.

The chapter-map tests run in process on hand-built TOCs. The end-to-end tests
write a tiny EPUB with zipfile and run the ingest in a subprocess, because
`pipeline.config` fixes BOOKERY_ROOT at import time (see test_config.py).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image

from pipeline import epub_ingest as E

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Chapter map
# ---------------------------------------------------------------------------


def _toc(*entries) -> list[E.TocEntry]:
    out = []
    for i, e in enumerate(entries):
        if isinstance(e, str):
            out.append(E.TocEntry(e, f"d{i}.xhtml", None))
        else:
            label, children = e
            out.append(E.TocEntry(label, f"d{i}.xhtml", None, _toc(*children) if isinstance(children, tuple) else children))
    return out


class _FakeEpub:
    def __init__(self, toc):
        self.toc = toc


def _plan(*entries, renumber=False):
    plans, skipped, _bounds, notes = E.plan_chapters(_FakeEpub(_toc(*entries)), renumber)
    return [(p.number, p.title) for p in plans], {s["label"]: s["reason"] for s in skipped}, notes


def test_numbered_chapters_with_an_introduction_and_a_conclusion():
    plans, skipped, _ = _plan(
        "Cover", "Title Page", "Contents", "Introduction: Algorithms to Live By",
        "1. Optimal Stopping: When to Stop Looking", "2. Explore/Exploit", "3. Sorting",
        "Conclusion: Computational Kindness", "Notes", "Index", "About the Authors",
    )
    assert plans == [
        (0, "Introduction: Algorithms to Live By"),
        (1, "Optimal Stopping: When to Stop Looking"),
        (2, "Explore/Exploit"),
        (3, "Sorting"),
        (4, "Conclusion: Computational Kindness"),
    ]
    assert set(skipped) == {"Cover", "Title Page", "Contents", "Notes", "Index", "About the Authors"}


def test_chapter_word_numbers_and_roman_numerals():
    plans, _, _ = _plan("CHAPTER ONE: Maximizing Utility", "CHAPTER TWO: Demand", "Chapter III")
    assert plans == [(1, "Maximizing Utility"), (2, "Demand"), (3, "Chapter III")]


def test_only_the_last_opener_before_chapter_one_becomes_chapter_zero():
    plans, skipped, _ = _plan("Foreword by Someone", "Introduction", "Chapter 1 ADHD 101", "Chapter 2 Diagnosis")
    assert plans[0] == (0, "Introduction")
    assert "Foreword by Someone" in skipped


def test_a_leading_number_in_a_title_is_not_a_chapter_number():
    plans, _, _ = _plan("Welcome", "Culture", "20 percent time", "Strategy", "Talent")
    # Too few numbered entries to trust: every entry is numbered in order.
    assert [n for n, _ in plans] == [1, 2, 3, 4, 5]
    assert plans[2] == (3, "20 percent time")


def test_parts_are_opened_and_their_chapters_carry_the_part():
    toc = _toc("Preface", ("Part I. Foundations", ("1. Introduction", "2. Basic Concepts")),
               ("Part II. Synchronization", ("3. Time", "4. Mutual Exclusion")), "Back Matter")
    plans, skipped, bounds, _ = E.plan_chapters(_FakeEpub(toc))
    assert [(p.number, p.title, p.part) for p in plans] == [
        (0, "Preface", None), (1, "Introduction", 1), (2, "Basic Concepts", 1),
        (3, "Time", 2), (4, "Mutual Exclusion", 2),
    ]
    assert [s["label"] for s in skipped] == ["Back Matter"]
    # The part entries still bound chapters: a part's own page belongs to none.
    assert any(b.label.startswith("Part II") for b in bounds)


def test_a_part_divider_with_no_content_of_its_own_is_skipped():
    toc = [
        E.TocEntry("PART 1 | START IT", "a.xhtml", None),
        E.TocEntry("1. Just Start", "a.xhtml", None),
        E.TocEntry("2. Ask", "b.xhtml", None),
    ]
    plans, skipped, _, _ = E.plan_chapters(_FakeEpub(toc))
    assert [(p.number, p.part) for p in plans] == [(1, 1), (2, 1)]
    assert skipped == [{"label": "PART 1 | START IT", "reason": "part divider"}]


def test_numbering_that_restarts_continues_the_sequence():
    plans, _, notes = _plan("1. One", "2. Two", "3. Three", "1. Appendix One", "2. Appendix Two")
    assert [n for n, _ in plans] == [1, 2, 3, 4, 5]
    assert plans[3] == (4, "Appendix One")
    assert notes and "restarts" in notes[0]


def test_flat_toc_sections_fold_into_their_chapter():
    toc = [
        E.TocEntry("Introduction", "intro.xhtml", None),
        E.TocEntry("The Finland plan", "intro.xhtml", "a1"),
        E.TocEntry("Speed", "intro.xhtml", "a2"),
        E.TocEntry("Culture", "ch1.xhtml", None),
    ]
    plans, _, bounds, _ = E.plan_chapters(_FakeEpub(toc))
    assert [p.title for p in plans] == ["Introduction", "Culture"]
    assert [c.label for c in plans[0].entry.children] == ["The Finland plan", "Speed"]
    assert [b.label for b in bounds] == ["Introduction", "Culture"]


def test_unnumbered_entries_between_numbered_chapters_are_sections():
    plans, skipped, _ = _plan("1. Start", "Begin Before You Are Ready", "2. Ask", "3. Build")
    assert [n for n, _ in plans] == [1, 2, 3]
    assert skipped["Begin Before You Are Ready"].startswith("folded")


def test_renumber_numbers_every_kept_entry():
    plans, _, _ = _plan("Introduction", "1. A", "2. B", renumber=True)
    assert plans == [(1, "Introduction"), (2, "A"), (3, "B")]


def test_vietnamese_chapter_and_part_prefixes():
    plans, _, _ = _plan("Mục lục", "PHẦN MỘT - 1. Muốn lấy mật", "2. Bí mật", "Chương 3")
    assert plans == [(1, "Muốn lấy mật"), (2, "Bí mật"), (3, "Chương 3")]


# ---------------------------------------------------------------------------
# A tiny EPUB, end to end
# ---------------------------------------------------------------------------

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>t</title></head><body>{}</body></html>"""

FILLER = ("This sentence is here so the paragraph reads like running prose in a real chapter, "
          "long enough that nobody would mistake it for a heading or a caption of any kind.")

CH1 = f"""
<span epub:type="pagebreak" id="page12" title="12"/>
<p class="CT">1&#160;&#160;Optimal Stopping</p>
<p class="CST">When to Stop Looking</p>
<p>Look at 37 percent<sup><a href="notes.xhtml#n1">1</a></sup> of the options first<a epub:type="noteref" href="#n2">2</a>. Area grows as r<sup>2</sup>. {FILLER}</p>
<p class="H1" id="s1">The Secretary Problem</p>
<p>Imagine interviewing applicants&nbsp;one at a time, where the chance of success is
<math alttext="1/e"><mi>e</mi></math> in the limit. {FILLER}</p>
<figure id="f1"><img src="../Images/curve.png" alt="A curve"/><figcaption>Figure 1.1 The chance of success converges on 37 percent.</figcaption></figure>
<span epub:type="pagebreak" title="13"/>
<a id="s2"/><h2>When to Park</h2>
<p class="img"><img src="../Images/park.png" alt="image"/></p>
<p class="CAP">How to optimally find parking.</p>
<div class="Para">Spots are spaced evenly <span class="InlineEquation"><img src="../Images/tiny.png" alt="$$d$$"/></span> apart.
<div class="Equation"><math alttext="\\frac{{1}}{{2}}"><mn>1</mn></math></div>
And then the text goes on. {FILLER}</div>
<ol><li>1. Already numbered</li><li>2. Also numbered</li></ol>
<aside epub:type="footnote"><p>*A footnote about parking.</p></aside>
"""

CH2 = f"""<h1>Explore/Exploit</h1><p>{FILLER}</p>
<table><caption>Table 2.1 Payoffs</caption><tr><th>Arm</th><th>Win</th></tr><tr><td>A</td><td>0.6</td></tr></table>"""


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _drawn_png(kind: str) -> bytes:
    """A section-break rule (one thin line), or a one-line formula image
    (ink across its whole height): the same size, only one is a figure."""
    im = Image.new("RGB", (288, 64), "white")
    px = im.load()
    for x in range(10, 278):
        for y in ((31, 32, 33) if kind == "rule" else range(8, 56) if x % 9 < 4 else ()):
            px[x, y] = (0, 0, 0)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


TINY_DOCS = {
    "cover": "<p>cover</p>",
    "intro": f"<h1>Introduction</h1><p>{FILLER}</p>",
    "ch1": CH1,
    "ch2": CH2,
    "notes": "<p>1. A note.</p>",
}
# (label, doc, [(child label, child href)]) -- hrefs relative to Text/.
TINY_TOC = [("Cover", "cover", []), ("Introduction", "intro", []),
            ("Chapter 1: Optimal Stopping", "ch1", [("The Secretary Problem", "ch1.xhtml#s1"),
                                                    ("When to Park", "ch1.xhtml#s2")]),
            ("Chapter 2: Explore/Exploit", "ch2", []), ("Notes", "notes", [])]
TINY_IMAGES = {"curve": (400, 300), "park": (300, 200), "tiny": (20, 12)}


def _write_epub(path: Path, *, nav: bool = True, docs: dict | None = None,
                entries: list | None = None, images: dict | None = None) -> Path:
    docs = docs or TINY_DOCS
    entries = entries or TINY_TOC
    images = images or TINY_IMAGES
    manifest = "".join(
        f'<item id="{k}" href="Text/{k}.xhtml" media-type="application/xhtml+xml"/>' for k in docs
    )
    manifest += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    manifest += "".join(
        f'<item id="{n}" href="Images/{n}.png" media-type="image/png"/>' for n in images
    )
    if nav:
        manifest += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    spine = "".join(f'<itemref idref="{k}"/>' for k in docs)
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{'3.0' if nav else '2.0'}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Tiny Book</dc:title>
<dc:creator>A. Author</dc:creator><dc:language>en</dc:language></metadata>
<manifest>{manifest}</manifest><spine toc="ncx">{spine}</spine></package>"""
    nav_doc = XHTML.format("<nav epub:type=\"toc\"><ol>" + "".join(
        f'<li><a href="Text/{d}.xhtml">{label}</a>'
        + ("<ol>" + "".join(f'<li><a href="Text/{h}">{cl}</a></li>' for cl, h in kids) + "</ol>" if kids else "")
        + "</li>"
        for label, d, kids in entries
    ) + "</ol></nav>")
    points = "".join(
        f'<navPoint id="n{i}"><navLabel><text>{label}</text></navLabel><content src="Text/{d}.xhtml"/>'
        + "".join(f'<navPoint id="n{i}{j}"><navLabel><text>{cl}</text></navLabel>'
                  f'<content src="Text/{h}"/></navPoint>' for j, (cl, h) in enumerate(kids))
        + "</navPoint>"
        for i, (label, d, kids) in enumerate(entries)
    )
    ncx = f'<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>{points}</navMap></ncx>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        if nav:
            z.writestr("OEBPS/nav.xhtml", nav_doc)
        for k, body in docs.items():
            z.writestr(f"OEBPS/Text/{k}.xhtml", XHTML.format(body))
        for name, size in images.items():
            z.writestr(f"OEBPS/Images/{name}.png", _drawn_png(size) if isinstance(size, str) else _png(*size))
    return path


def _ingest(root: Path, epub: Path, *flags: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "BOOKERY_ROOT": str(root)}
    return subprocess.run(
        [sys.executable, "-m", "pipeline.epub_ingest", "--epub", str(epub), *flags],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def test_list_prints_the_map_and_writes_nothing(tmp_path):
    epub = _write_epub(tmp_path / "tiny.epub")
    proc = _ingest(tmp_path / "book", epub, "--list")
    assert proc.returncode == 0, proc.stderr
    assert "ch00  Introduction" in proc.stdout
    assert "ch01  Optimal Stopping" in proc.stdout
    assert "skip  Notes" in proc.stdout
    assert not (tmp_path / "book" / "work").exists()


def _ingested(tmp_path, *, nav=True):
    epub = _write_epub(tmp_path / "tiny.epub", nav=nav)
    root = tmp_path / "book"
    proc = _ingest(root, epub)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    work = root / "work"
    model = json.loads((work / "reconcile" / "ch01.json").read_text())
    return root, json.loads((work / "triage.json").read_text()), model


def test_ingest_writes_triage_and_the_canonical_model(tmp_path):
    root, triage, model = _ingested(tmp_path)
    assert triage["source"]["format"] == "epub"
    assert triage["source"]["title"] == "Tiny Book"
    assert triage["epub"]["toc"] == "nav"
    assert [c["number"] for c in triage["boundary_map"]["chapters"]] == [0, 1, 2]
    ch1 = triage["boundary_map"]["chapters"][1]
    # Print page markers give printed pages, with an offset of zero.
    assert (ch1["printed_page"], ch1["pdf_page"], ch1["pdf_page_end"]) == (12, 12, 13)
    assert [s["title"] for s in ch1["sections"]] == ["The Secretary Problem", "When to Park"]
    assert (root / "epub" / "book.epub").exists()

    blocks = model["blocks"]
    heads = [(b["text"], b["level"]) for b in blocks if b["type"] == "heading"]
    assert heads == [("1 Optimal Stopping", 1), ("When to Stop Looking", 1),
                     ("The Secretary Problem", 2), ("When to Park", 2)]
    assert {b["page"] for b in blocks} == {12, 13}


def test_text_keeps_math_and_entities_and_drops_page_markers(tmp_path):
    _root, _triage, model = _ingested(tmp_path)
    text = " ".join(b.get("text", "") for b in model["blocks"])
    assert "applicants one at a time" in text      # &nbsp; became a space
    assert "$1/e$" in text                          # MathML as its TeX
    assert "spaced evenly $d$ apart." in text       # an inline TeX image
    assert "And then the text goes on." in text     # text after a block child
    assert "Look at 37 percent of the options first." in text   # note markers dropped
    assert "Area grows as r2." in text              # a superscript that isn't a note stays
    assert "12" not in text.split()                 # the page marker's own text
    eq = next(b for b in model["blocks"] if b["type"] == "equation")
    assert eq["latex"] == "\\frac{1}{2}"


def test_figures_bind_captions_and_export_images(tmp_path):
    root, _triage, model = _ingested(tmp_path)
    figs = [b for b in model["blocks"] if b["type"] == "figure"]
    assert len(figs) == 2
    labelled, plain = figs
    assert (labelled["label"], labelled["label_kind"]) == ("1.1", "figure")
    assert labelled["caption"] == "The chance of success converges on 37 percent."
    assert labelled["asset"] == "img/ch01/fig-1-1.png"
    assert plain["caption"] == "How to optimally find parking."
    assert plain["alt"] == "How to optimally find parking."   # "image" is a placeholder
    assert not any(b.get("text") == "How to optimally find parking." for b in model["blocks"])
    assets = json.loads((root / "work" / "assets.json").read_text())["ch01"]
    assert [a["block"] for a in assets] == [labelled["id"], plain["id"]]
    for a in assets:
        assert (root / "site" / "static" / a["file"]).exists()


def test_lists_footnotes_and_tables(tmp_path):
    root, _triage, model = _ingested(tmp_path)
    lst = next(b for b in model["blocks"] if b["type"] == "list")
    assert lst["text"] == "1. Already numbered\n2. Also numbered"
    assert any(b["type"] == "footnote" and "footnote about parking" in b["text"] for b in model["blocks"])
    ch2 = json.loads((root / "work" / "reconcile" / "ch02.json").read_text())
    table = next(b for b in ch2["blocks"] if b["type"] == "table")
    assert (table["label"], table["label_kind"], table["caption"]) == ("2.1", "table", "Payoffs")
    assert table["cells"] == [["Arm", "Win"], ["A", "0.6"]]


def test_ncx_only_epub_gives_the_same_chapters(tmp_path):
    _root, triage, model = _ingested(tmp_path, nav=False)
    assert triage["epub"]["toc"] == "ncx"
    assert [c["number"] for c in triage["boundary_map"]["chapters"]] == [0, 1, 2]
    assert sum(1 for b in model["blocks"] if b["type"] == "heading") == 4


def test_refuses_to_overwrite_a_pdf_book(tmp_path):
    epub = _write_epub(tmp_path / "tiny.epub")
    root = tmp_path / "book"
    (root / "work").mkdir(parents=True)
    (root / "work" / "triage.json").write_text(json.dumps({"source": {"format": "pdf"}}))
    proc = _ingest(root, epub)
    assert proc.returncode == 1
    assert "refusing" in proc.stderr
    assert _ingest(root, epub, "--force").returncode == 0


def test_learn_reads_an_ingested_chapter(tmp_path):
    root, _triage, _model = _ingested(tmp_path)
    from learn.source import ChapterSource

    src = ChapterSource(1, root)
    assert src.book == {"title": "Tiny Book", "author": "A. Author", "format": "epub"}
    assert [s.title for s in src.sections] == ["The Secretary Problem", "When to Park"]
    assert [ex.ref for ex in src.exhibits][0] == "fig 1.1"
    assert src.resolve_exhibit("Figure 1.1").file.exists()
    first_para = next(b for b in src.blocks if b["type"] == "paragraph")
    assert src.printed_page(first_para) == 12


# One file per TOC entry, each restarting at <h1> (Vellum, Bookmate and
# similar exports), with a quote-mark graphic on every blockquote.
QUOTE = '<blockquote><div class="blockquote-image-block"><img src="../Images/quote.png"/></div><p>{}</p></blockquote>'
SPLIT_DOCS = {
    "part": "<h1>Audience Discovery</h1>",
    "guide": f"<h1>A Guide to Finding Your Audience</h1><p>{FILLER}</p>" + QUOTE.format("Start with people."),
    "communities": (f"<h1>Communities</h1><h2>Finding Communities</h2><p>{FILLER}</p>"
                    f"<h2>Kinds of Communities</h2><p>{FILLER}</p><h3>Goal-Driven</h3><p>{FILLER}</p>"
                    + QUOTE.format("Dwell, don't sell.")),
    "tally": (f"<h1>Tallying the Results</h1><p>{FILLER}</p>" + QUOTE.format("Pick one.")
              + '<p><img src="../Images/rule.png"/></p><p><img src="../Images/formula.png"/></p>'
              + '<p><img src="../Images/chart.png" alt="The ranked list"/></p>'),
}
SPLIT_TOC = [("Audience Discovery", "part", [("A Guide to Finding Your Audience", "guide.xhtml"),
                                             ("Communities", "communities.xhtml"),
                                             ("Tallying the Results", "tally.xhtml")])]
SPLIT_IMAGES = {"quote": (144, 116), "chart": (500, 300), "rule": "rule", "formula": "formula"}


def test_headings_in_a_file_per_section_book_nest_under_their_toc_entry(tmp_path):
    epub = _write_epub(tmp_path / "split.epub", docs=SPLIT_DOCS, entries=SPLIT_TOC, images=SPLIT_IMAGES)
    root = tmp_path / "book"
    proc = _ingest(root, epub)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    model = json.loads((root / "work" / "reconcile" / "ch01.json").read_text())
    heads = [(b["text"], b["level"]) for b in model["blocks"] if b["type"] == "heading"]
    assert heads == [("Audience Discovery", 1), ("A Guide to Finding Your Audience", 2),
                     ("Communities", 2), ("Finding Communities", 3), ("Kinds of Communities", 3),
                     ("Goal-Driven", 4), ("Tallying the Results", 2)]
    from learn.source import ChapterSource

    assert [s.title for s in ChapterSource(1, root).sections] == [
        "A Guide to Finding Your Audience", "Communities", "Tallying the Results"]


def test_an_image_the_book_repeats_everywhere_is_not_a_figure(tmp_path):
    epub = _write_epub(tmp_path / "split.epub", docs=SPLIT_DOCS, entries=SPLIT_TOC, images=SPLIT_IMAGES)
    root = tmp_path / "book"
    assert _ingest(root, epub).returncode == 0
    model = json.loads((root / "work" / "reconcile" / "ch01.json").read_text())
    figs = [b for b in model["blocks"] if b["type"] == "figure"]
    # The quote mark (used three times) and the rule are decoration; the
    # formula image and the chart are content.
    assert [f.get("alt") for f in figs] == [None, "The ranked list"]
    assert figs[0]["asset"].endswith(".png")
    assert "Dwell, don't sell." in " ".join(b.get("text", "") for b in model["blocks"])


def test_back_matter_the_toc_leaves_out_does_not_join_the_last_chapter(tmp_path):
    docs = {**TINY_DOCS, "notes": f"<h1>Conclusion</h1><p>{FILLER}</p>",
            "author": "<h1>ABOUT THE AUTHOR</h1><p>She has written many books about stopping.</p>",
            "ads": "<h2>Work with the author</h2><p>Book a two-day workshop.</p>"}
    entries = TINY_TOC[:-1] + [("Conclusion", "notes", [])]
    epub = _write_epub(tmp_path / "trail.epub", docs=docs, entries=entries)
    root = tmp_path / "book"
    proc = _ingest(root, epub)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skip  ABOUT THE AUTHOR" in proc.stdout
    model = json.loads((root / "work" / "reconcile" / "ch03.json").read_text())
    text = " ".join(b.get("text", "") for b in model["blocks"])
    assert "Conclusion" in text and "running prose" in text
    assert "ABOUT THE AUTHOR" not in text and "workshop" not in text
