"""Regression tests for the pure text-normalisation functions.

These use synthetic inputs rather than a real PDF fixture, since the module
under test is pure text processing -- see `pipeline/textnorm.py` for the
extraction-behaviour rationale behind each rule.
"""

from __future__ import annotations

from pipeline import textnorm


def test_split_accents_normalise_to_the_same_precomposed_form():
    pymupdf_form = "Erd\u00a8os"  # spacing accent glyph before the base letter
    pdftotext_form = "Erdo\u0308s"  # base letter before a combining mark
    assert textnorm.repair_accents(pymupdf_form) == textnorm.repair_accents(pdftotext_form)
    assert textnorm.normalize(pymupdf_form) == textnorm.normalize(pdftotext_form)


def test_repair_accents_recombines_common_diacritics():
    assert textnorm.repair_accents("Erd\u00a8os") == "Erd\u00f6s"
    assert textnorm.repair_accents("Tam\u00b4as") == "Tam\u00e1s"
    assert textnorm.repair_accents("\u00b4Eva") == "\u00c9va"


def test_normalize_collapses_ligatures_quotes_dashes_and_case():
    assert textnorm.normalize("\ufb01le") == "file"
    assert textnorm.normalize("\u201cquoted\u201d") == '"quoted"'
    assert textnorm.normalize("well\u2013known") == "well-known"
    assert textnorm.normalize("MiXeD Case") == "mixed case"


def test_normalize_rejoins_hyphenated_line_breaks():
    assert textnorm.normalize("net-\nwork") == "network"


def test_tokens_are_lowercase_words():
    assert textnorm.tokens("Two Nodes, One Edge.") == ["two", "nodes", "one", "edge"]


def test_extract_citations_separates_brackets_from_content_numbers():
    text = "this claim has support [12] and further support [7, 8]"
    stripped, refs = textnorm.extract_citations(text)
    assert refs == [12, 7, 8]
    assert "12" not in stripped


def test_numbers_finds_bare_decimals_without_a_leading_digit():
    # A regex requiring a leading digit misses ".48"; both sides of a
    # fidelity comparison would then silently agree while missing it.
    assert textnorm.numbers("the probability is .48, not .12") == ["0.48", "0.12"]


def test_numbers_keeps_ordinal_suffixes_countable():
    assert textnorm.numbers("the 500th and 501st nodes") == ["500", "501"]


def test_numbers_excludes_citation_brackets_by_default():
    assert textnorm.numbers("see reference [421] for the proof") == []


def test_numbers_normalises_trailing_zero_decimals_but_not_integers():
    assert textnorm.numbers("2.0 grams versus 2 grams") == ["2", "2"]


def test_is_integer_soup_detects_long_bare_number_runs():
    axis_ticks = "0 10 20 30 40 50 60 70 80 90 100"
    assert textnorm.is_integer_soup(axis_ticks)


def test_is_integer_soup_ignores_ordinary_prose_with_numbers():
    prose = "there are 3 nodes and 4 edges, so the graph has 7 elements in total"
    assert not textnorm.is_integer_soup(prose)


def test_is_integer_soup_requires_a_minimum_run_length():
    assert not textnorm.is_integer_soup("1 2 3")


def test_collect_hyphenated_forms_reads_intact_midline_occurrences():
    forms = textnorm.collect_hyphenated_forms("a well-known result about well-known graphs")
    assert "well-known" in forms


def test_join_hyphenated_rejoins_only_attested_forms():
    known = {"well-known"}
    assert textnorm.join_hyphenated("a well-", "known result", known) == "a well-known result"
    # A form not attested elsewhere in the document is discretionary TeX
    # hyphenation at the line break, so the hyphen is dropped on rejoin.
    assert textnorm.join_hyphenated("a net-", "work protocol", known) == "a network protocol"


def test_join_hyphenated_keeps_digit_ranges_across_an_en_dash():
    assert textnorm.join_hyphenated("pages 1872\u2013", "81", set()) == "pages 1872\u201381"


def test_for_output_expands_ligatures_but_keeps_accents():
    assert textnorm.for_output("o\ufb03ce") == "office"
    assert textnorm.for_output("Erd\u00a8os") == "Erd\u00f6s"
