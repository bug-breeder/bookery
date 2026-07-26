"""Regression tests for the known artifacts in the validation fixture.

Each test corresponds to a numbered artifact in the project brief. They run
against the real PDF because these are extraction behaviours, not pure
functions -- a synthetic fixture would not reproduce them.
"""

from __future__ import annotations

import json
import re

import fitz
import pytest

from pipeline import config, pdfutil, textnorm
from verify import candidate, gates, reference

pytestmark = pytest.mark.skipif(
    not config.DEFAULT_PDF.exists(), reason="validation PDF not fetched"
)


@pytest.fixture(scope="module")
def doc() -> fitz.Document:
    return fitz.open(config.DEFAULT_PDF)


@pytest.fixture(scope="module")
def triage() -> dict:
    return json.loads(config.TRIAGE_JSON.read_text())


@pytest.fixture(scope="module")
def ch02(doc: fitz.Document) -> reference.Reference:
    return reference.build_reference(doc, 37, 60)


# -- Artifact 1: running headers merge page numbers into titles -------------


def test_running_headers_are_assembled_then_stripped(ch02):
    headers = [r.text for r in ch02.removed if r.reason == "running_header"]
    assert "24 CHAPTER 2. GRAPHS" in headers
    assert "2.2. PATHS AND CONNECTIVITY 25" in headers
    assert "CHAPTER 2. GRAPHS" not in ch02.body_text


def test_every_body_page_has_its_header_removed(ch02):
    pages_with_header = {r.page for r in ch02.removed if r.reason == "running_header"}
    # Page 37 is the chapter opener and carries no running head.
    assert pages_with_header == set(range(38, 61))


def test_real_section_heading_survives(ch02):
    # The heading '2.2 Paths and Connectivity' must not be mistaken for the
    # all-caps running head of the same name.
    assert "Paths and Connectivity" in ch02.body_text


# -- Artifact 2: repeated per-chapter footer boilerplate -------------------


def test_chapter_footer_boilerplate_is_stripped(ch02):
    footers = [r.text for r in ch02.removed if r.reason == "footer_boilerplate"]
    assert any("Cambridge University Press" in f for f in footers)
    assert "Draft version" not in ch02.body_text


def test_opener_folio_is_stripped(ch02):
    folios = [r.text for r in ch02.removed if r.reason == "page_folio"]
    assert "23" in folios


# -- Artifact 3: figure node labels extracted as body text -----------------


def test_karate_club_integer_soup_is_detected():
    soup = "27 15 23 10 20 4 13 16 34 31 14 12 18 17 30 33 32 9 2 1 5 6 21"
    assert textnorm.is_integer_soup(soup)


def test_axis_tick_labels_are_detected():
    assert textnorm.is_integer_soup("100 90 80 70 60 50 40 30 20 10 0")


def test_prose_containing_numbers_is_not_soup():
    assert not textnorm.is_integer_soup(
        "there are 3 nodes and 4 edges, so the graph has 7 elements in total"
    )
    assert not textnorm.is_integer_soup("1.0 2.0")


# -- Artifact 4: broken combining characters -------------------------------


def test_both_extractors_normalise_to_the_same_accented_form():
    pymupdf_form = "Erd\u00a8os"
    pdftotext_form = "Erdo\u0308s"
    assert textnorm.repair_accents(pymupdf_form) == textnorm.repair_accents(pdftotext_form)
    assert textnorm.normalize(pymupdf_form) == textnorm.normalize(pdftotext_form)


def test_erdos_keeps_the_diaeresis_the_book_prints():
    # The book prints a diaeresis, verified visually at 400dpi on p.51.
    # Rewriting it to the Hungarian double acute would be correcting the
    # authors, which the brief forbids.
    assert textnorm.repair_accents("Erd\u00a8os") == "Erd\u00f6s"
    assert "\u0151" not in textnorm.repair_accents("Erd\u00a8os")


def test_eva_tardos_recovers_the_capital_e():
    assert textnorm.repair_accents("\u00b4Eva Tardos") == "\u00c9va Tardos"
    assert textnorm.repair_accents("E\u0301va Tardos") == "\u00c9va Tardos"


def test_other_accented_names():
    assert textnorm.repair_accents("Tam\u00b4as") == "Tam\u00e1s"
    assert textnorm.repair_accents("Barab\u00b4asi") == "Barab\u00e1si"
    assert textnorm.repair_accents("R\u00b4eka") == "R\u00e9ka"


# -- Artifact 5: small-caps node names -------------------------------------


def test_small_caps_node_names_are_not_uppercased(doc):
    ref = reference.build_reference(doc, 37, 60)
    body = ref.body_text
    # The Arpanet figure discussion names hosts in small caps, which extract
    # lowercased. They must survive exactly as extracted.
    assert "ucla" in body.lower()
    assert "UCLA" not in body or "ucla" in body


# -- Artifact 6: bracketed numeric citations -------------------------------


def test_citations_are_separated_from_content_numbers():
    text = "the karate club [421] has 34 members and [297, 391] agree"
    stripped, refs = textnorm.extract_citations(text)
    assert refs == [421, 297, 391]
    assert "421" not in stripped
    assert textnorm.numbers(text) == ["34"]


# -- Artifact 7: payoff matrices -------------------------------------------


def test_payoff_matrix_cells_extract_as_text(doc):
    # Figure 6.1 is a 2x2 payoff matrix set as text, not as an image, so its
    # cell values must be recoverable rather than lost inside a crop.
    rows = [l.text for l in pdfutil.page_lines(doc, 172)]
    joined = " ".join(rows)
    for cell in ("90, 90", "86, 92", "92, 86", "88, 88"):
        assert cell in joined


# -- Structural integrity of the boundary map ------------------------------


def test_boundary_map_offset_is_uniform(triage):
    offset = triage["boundary_map"]["printed_to_pdf_offset"]
    for ch in triage["boundary_map"]["chapters"]:
        # The Preface (chapter 0) is front matter paginated in lowercase
        # roman numerals, a distinct sequence from the arabic body pagination
        # this offset describes; see pipeline/stage0_acquire.py.
        if ch["number"] == 0:
            continue
        assert ch["pdf_page"] - ch["printed_page"] == offset


def test_every_chapter_opener_reads_chapter_n(triage):
    assert all(c["ok"] for c in triage["spot_checks"])
    assert len(triage["spot_checks"]) == 24


def test_preface_is_modelled_as_chapter_zero(triage):
    # The Preface is front matter with no ToC entry of its own, so it cannot
    # be found by the same "N Title .... page" regex as a numbered chapter.
    # It is injected as a synthetic chapter 0 instead, which lets it reuse
    # every stage and gate a numbered chapter gets rather than needing a
    # bespoke path -- see pipeline/stage0_acquire.py.
    preface = next(c for c in triage["boundary_map"]["chapters"] if c["number"] == 0)
    assert preface["title"] == "Preface"
    assert preface["part"] is None
    assert preface["pdf_page"] == triage["boundary_map"]["preface_pdf_page"]
    assert preface["pdf_page_end"] < triage["boundary_map"]["body_first_pdf_page"]


def test_chapters_do_not_absorb_part_dividers(triage):
    dividers = {p["pdf_page"] for p in triage["boundary_map"]["parts"]}
    for ch in triage["boundary_map"]["chapters"]:
        span = set(range(ch["pdf_page"], ch["pdf_page_end"] + 1))
        assert not (span & dividers), f"chapter {ch['number']} absorbed a part divider"


@pytest.mark.parametrize("number", [2, 6, 14, 23])
def test_section_counts_match_the_toc(doc, triage, number):
    ch = next(c for c in triage["boundary_map"]["chapters"] if c["number"] == number)
    counts = reference.count_structures(doc, ch["pdf_page"], ch["pdf_page_end"]).as_dict()
    assert counts["sections"] == [s["number"] for s in ch["sections"]]


@pytest.mark.parametrize(
    "number,expected_figures",
    [(2, 14), (6, 28), (14, 22)],
)
def test_figure_labels_are_contiguous(doc, triage, number, expected_figures):
    ch = next(c for c in triage["boundary_map"]["chapters"] if c["number"] == number)
    counts = reference.count_structures(doc, ch["pdf_page"], ch["pdf_page_end"]).as_dict()
    labels = counts["figures"]
    assert len(labels) == expected_figures
    assert labels == [f"{number}.{i}" for i in range(1, expected_figures + 1)]


# -- Candidate rendering ---------------------------------------------------


def test_markdown_to_text_excludes_math_like_an_image():
    # Math is excluded from the candidate text entirely, not reduced to its
    # operands: a formula's rendered glyphs are not a character-for-character
    # reading of the LaTeX that reproduces them, so diffing the two would
    # compare things that were never meant to match (see candidate.py). Math
    # is instead verified independently: count/labels in the structural
    # gate, and KaTeX strict-mode parsing in the math gate.
    md = r"The value is $\frac{n(n-1)}{2}$ and $\delta$ is small."
    text = candidate.markdown_to_text(md)
    assert "frac" not in text
    assert "\u03b4" not in text
    assert set(textnorm.tokens(text)) >= {"value", "small"}


def test_markdown_to_text_keeps_link_text_and_drops_images():
    md = "See [Figure 2.6](/part1/02-graphs#fig-2-6).\n\n![A graph](/img/ch02/fig-2-6.png)"
    text = candidate.markdown_to_text(md)
    assert "Figure 2.6" in text
    assert "fig-2-6.png" not in text


# --------------------------------------------------------------------------
# Defects found during Chapter 2 adjudication. Each of these was a real loss
# or misclassification that the coverage and numeric gates could not see,
# because both sides of the comparison were wrong in the same way.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ch02_model() -> dict:
    path = config.RECONCILE_DIR / "ch02.json"
    if not path.exists():
        pytest.skip("chapter 2 has not been reconciled")
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def ch02_markdown() -> str:
    matches = sorted(config.DOCS.rglob("02-*.md"))
    if not matches:
        pytest.skip("chapter 2 has not been emitted")
    return matches[0].read_text()


def test_multiline_caption_is_kept_whole(ch02_model):
    """A caption runs to several lines; only the first carries the label.

    Without the extractors' caption extents the remainder was emitted as body
    prose sitting loose after the figure.
    """
    figure = next(
        b for b in ch02_model["blocks"] if b["type"] == "figure" and b["label"] == "2.4"
    )
    caption = figure["caption"]
    assert caption.startswith("Images of graphs arising in different domains.")
    # Text from the last line of the caption, four lines below the label.
    assert "Tank Street Bridge" in caption
    assert "rigidity theory" in caption


def test_caption_text_is_not_swallowed_as_figure_interior(ch02_model):
    """Figure bboxes must stop above their caption.

    A picture region that covers the caption makes the caption count as figure
    interior, which drops it from the page *and* from the reference -- a loss
    no gate can see because both sides lose it identically.
    """
    figures = {
        b["label"]: b for b in ch02_model["blocks"] if b["type"] == "figure" and b.get("label")
    }
    for label in ("2.7", "2.10", "2.11"):
        figure = figures[label]
        caption_top = min(
            b["bbox"][1]
            for b in ch02_model["blocks"]
            if b["type"] == "caption" and b.get("label") == label
        )
        assert figure["bbox"][3] <= caption_top, f"figure {label} overlaps its caption"


def test_small_caps_are_restyled_not_rewritten(ch02_model, ch02_markdown):
    """MIT is set in small caps and extracts as "mit".

    The characters must not change -- rewriting to "MIT" would put text in the
    output that the source does not contain -- so the casing is restored with
    styling instead.
    """
    figure = next(
        b for b in ch02_model["blocks"] if b["type"] == "figure" and b["label"] == "2.9"
    )
    assert "mit" in figure["small_caps"]
    assert '<span class="node">mit</span>' in ch02_markdown
    assert "at the node MIT" not in ch02_markdown


def test_exercise_subparts_are_not_captions(ch02_model):
    """"(a) ..." opens a figure sub-caption or an exercise sub-part.

    Sub-captions are set smaller than body text; exercise sub-parts are body
    size. Without that test every exercise sub-part was styled as a caption.
    """
    parts = [b for b in ch02_model["blocks"] if b["type"] == "exercise_part"]
    assert len(parts) >= 6
    assert any(p["text"].startswith("(a) Give an example of a graph") for p in parts)
    captions = [b["text"] for b in ch02_model["blocks"] if b["type"] == "caption"]
    assert not any(c.startswith("(a) Give an example") for c in captions)
    # Genuine figure sub-captions must still be captions.
    assert any(c.startswith("(a) A graph on 4 nodes") for c in captions)


def test_exercises_are_split_into_separate_blocks(ch02_model):
    exercises = [b for b in ch02_model["blocks"] if b["type"] == "exercise"]
    assert len(exercises) == 3
    assert [e["text"][:2] for e in exercises] == ["1.", "2.", "3."]


def test_heading_anchor_syntax_is_not_content(ch02_markdown):
    """"{#sec-2-1}" is markup; its digits must not count as numeric literals."""
    assert "{#sec-2-1}" in ch02_markdown
    text = candidate.markdown_to_text(ch02_markdown)
    assert "sec-2-1" not in text
    assert "{#" not in text


def test_alt_text_cannot_break_the_image_syntax(ch02_model, ch02_markdown):
    """A bracket in alt text terminates the Markdown image early.

    Captions carry citation markers such as "[214]". Carried into alt text,
    the "[" ended the image syntax and six figures rendered as literal prose
    while the PNGs sat correctly on disk, so a gate that only checked the
    filesystem saw nothing wrong.
    """
    figures = [b for b in ch02_model["blocks"] if b["type"] == "figure" and b.get("alt")]
    assert figures
    for figure in figures:
        assert "[" not in figure["alt"] and "]" not in figure["alt"]
    assert ch02_markdown.count("\n![") == 14


def test_every_figure_reaches_the_built_page():
    """Existing on disk is not the same as reaching the reader."""
    pages = sorted((config.SITE / "build").rglob("02-*/index.html"))
    if not pages:
        pytest.skip("site has not been built")
    html = pages[0].read_text()
    rendered = {m for m in re.findall(r"/assets/images/(fig-[0-9-]+)-", html)}
    assert len(rendered) == 14, f"only {len(rendered)} of 14 figures rendered"


# --------------------------------------------------------------------------
# Payoff matrices (chapter 6). TeX draws these with rules rather than as a
# tabular environment, so neither extractor reports a table and 24 of the
# chapter's 28 captioned exhibits were originally lost.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ch06_model() -> dict:
    path = config.RECONCILE_DIR / "ch06.json"
    if not path.exists():
        pytest.skip("chapter 6 has not been reconciled")
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def ch06_markdown() -> str:
    matches = sorted(config.DOCS.rglob("06-*.md"))
    if not matches:
        pytest.skip("chapter 6 has not been emitted")
    return matches[0].read_text()


def test_every_captioned_exhibit_binds_to_something(ch06_model):
    """All 28 of chapter 6's captions must find a figure or a matrix.

    Only 4 did before matrices were recovered: the rest were text grids no
    extractor reported, so their captions were orphaned and their payoffs were
    left loose in the prose.
    """
    counts = ch06_model["counts"]
    assert len(counts["figures"]) == 28
    assert counts["matrices"] == 42
    unbound = [
        b["label"]
        for b in ch06_model["blocks"]
        if b["type"] == "caption"
        and b.get("label")
        and "caption_without_figure" in (b.get("flags") or [])
    ]
    assert unbound == []


def test_payoff_matrix_values_and_axes(ch06_model):
    """Figure 6.1 cell by cell, including both players' names."""
    matrix = next(
        b for b in ch06_model["blocks"] if b["type"] == "matrix" and b.get("label") == "6.1"
    )
    assert matrix["cells"] == [
        ["You \\ Your Partner", "Presentation", "Exam"],
        ["Presentation", "90, 90", "86, 92"],
        ["Exam", "92, 86", "88, 88"],
    ]


def test_matrices_are_counted_as_figures_not_tables(ch06_model):
    """The book captions these "Figure N.N" and refers to them that way.

    Counting them as tables would make the model disagree with the PDF's own
    captions, and would break every "see Figure 6.1" in the prose.
    """
    assert ch06_model["counts"]["tables"] == []
    labels = {b.get("label") for b in ch06_model["blocks"] if b["type"] == "matrix"}
    assert "6.1" in labels
    assert "6.1" in ch06_model["counts"]["figures"]


def test_bare_decimal_payoffs_are_recovered(ch06_model):
    """The Marketing Strategy game writes its payoffs as ".48, .12".

    Requiring a leading digit lost this exhibit entirely, and also made every
    such number invisible to the numeric gate -- on both sides, so it passed.
    """
    matrix = next(
        b for b in ch06_model["blocks"] if b["type"] == "matrix" and b.get("label") == "6.5"
    )
    assert matrix["cells"][1][1] == ".48, .12"
    assert textnorm.numbers(".48, .12") == ["0.48", "0.12"]


def test_signed_payoffs_are_recovered(ch06_model):
    """Matching Pennies writes its payoffs with an explicit plus."""
    matrix = next(
        b for b in ch06_model["blocks"] if b["type"] == "matrix" and b.get("label") == "6.14"
    )
    assert matrix["cells"][1][1] == "\u22121, +1"


def test_degenerate_one_cell_matrix_is_recovered(ch06_model):
    """Iterated deletion leaves Figure 6.22 with a single surviving outcome.

    Requiring two rows would drop it; a lone payoff is believed only because a
    caption sits directly beneath it.
    """
    matrix = next(
        b for b in ch06_model["blocks"] if b["type"] == "matrix" and b.get("label") == "6.22"
    )
    assert matrix["cells"] == [["Firm 1 \\ Firm 2", "D"], ["C", "3, 3"]]


def test_ordinals_are_countable():
    """"the 500th and 501st nodes" carries two numbers."""
    assert textnorm.numbers("the 500th and 501st nodes") == ["500", "501"]
    # Digits inside an identifier are still not numbers.
    assert textnorm.numbers("v2 x3d") == []


def test_matrix_is_emitted_as_a_table_not_an_image(ch06_markdown):
    matrix_block = "\n".join(
        [
            "| **You \\\\ Your Partner** | **Presentation** | **Exam** |",
            "| --- | --- | --- |",
            "| **Presentation** | 90, 90 | 86, 92 |",
            "| **Exam** | 92, 86 | 88, 88 |",
        ]
    )
    assert matrix_block in ch06_markdown
    assert '<a id="fig-6-1"></a>' in ch06_markdown
    # Three true diagrams, and only those, are images.
    assert ch06_markdown.count("\n![") == 3


def test_wrapped_section_heading_is_joined(ch06_model):
    """Section 6.8's title wraps, and the break falls inside a word.

    Ending the heading at its first line left "ysis" as body prose and split
    "Analysis" in two.
    """
    heading = next(
        b for b in ch06_model["blocks"] if b["type"] == "heading" and b.get("label") == "6.8"
    )
    assert heading["text"] == "6.8 Mixed Strategies: Examples and Empirical Analysis"
    assert not any(b.get("text") == "ysis" for b in ch06_model["blocks"])


def test_space_after_an_f_ligature_is_recovered(doc, ch06_markdown):
    """A space following an f-ligature is encoded as positioning, not a character.

    The text layer holds "payoffmatrix" and "payoffof" where the page plainly
    reads "payoff matrix" and "payoff of". Because the reference text is built
    from the same text layer, both sides of every gate were wrong in the same
    way and the fused words reached the reader. Only the glyph positions show
    the space, so the line must be assembled character by character.
    """
    line = next(
        l
        for l in pdfutil.page_lines(doc, 172)
        if "matrix as in Figure 6.1" in l.text
    )
    assert "payo\ufb00 matrix" in line.text
    assert "payo\ufb00matrix" not in line.text

    assert "payoffmatrix" not in ch06_markdown
    assert "payoffof" not in ch06_markdown
    assert "payoff matrix" in ch06_markdown


def test_math_spacing_survives_character_level_joining(doc):
    """"1 - q" must not become "1 -q".

    The gap between the minus and the variable is only a little over 0.15em, so
    a threshold chosen to suit the ligature case closes it up.
    """
    line = next(
        l for l in pdfutil.page_lines(doc, 190) if "with probability 1" in l.text
    )
    assert "1 \u2212 q." in line.text


def test_payoff_cell_check_detects_a_corrupted_cell(doc, ch06_model, ch06_markdown):
    """The payoff checks must be able to fail, or they prove nothing."""
    from verify import gates, runner

    pdf_cells = runner._pdf_payoff_cells(doc, 169, 222)
    assert gates.check_payoff_cells(pdf_cells, ch06_markdown)["ok"]

    # One digit changed anywhere in the chapter must be caught.
    corrupted = ch06_markdown.replace("| 90, 90 |", "| 90, 91 |", 1)
    assert corrupted != ch06_markdown
    assert not gates.check_payoff_cells(pdf_cells, corrupted)["ok"]


def test_payoff_placement_check_detects_a_transposed_matrix(doc, ch06_model):
    """A cell in the wrong column is invisible to an order-free comparison."""
    from verify import gates

    matrices = [b for b in ch06_model["blocks"] if b["type"] == "matrix"]
    assert gates.check_payoff_placement(matrices, doc)["ok"]

    swapped = json.loads(json.dumps(matrices))
    target = next(m for m in swapped if m.get("label") == "6.1")
    target["cells"][1][1], target["cells"][1][2] = (
        target["cells"][1][2],
        target["cells"][1][1],
    )
    result = gates.check_payoff_placement(swapped, doc)
    assert not result["ok"]
    assert any(v["label"] == "6.1" for v in result["violations"])


# -- Gate 4 must not confuse "zero figures" with "gate did not run" ---------


def test_gate_assets_passes_a_chapter_with_no_figures(tmp_path):
    """A figure-free chapter (the Preface) must be able to reach PASS.

    Gate 5 already treats zero equations as a vacuous PASS rather than a
    block; this is the same fix for gate 4, which used to return BLOCKED
    for an empty assets list regardless of whether that meant "stage3 ran
    and found nothing to crop" or "stage3 has not run yet". The caller (see
    verify/runner.py) is responsible for telling those two apart by checking
    the assets.json key; this gate should never itself refuse a chapter that
    genuinely has no figures.
    """
    result = gates.gate_assets([], referenced_labels=set(), root=tmp_path)
    assert result.status == gates.PASS
    assert result.detail["assets"] == 0


def test_gate_assets_still_fails_a_missing_referenced_figure(tmp_path):
    """The vacuous-pass fix must not also hide a real missing figure."""
    result = gates.gate_assets([], referenced_labels={"2.1"}, root=tmp_path)
    assert result.status == gates.FAIL
    assert {"label": "2.1", "issue": "referenced_but_absent"} in result.detail["problems"]
