"""Display equation recovery from Marker's structural output.

Unlike payoff matrices, which neither extractor sees at all, Marker's layout
model does recognise TeX-typeset display math as a distinct block and -- in
fast mode, with no LLM pass -- already reduces it to a serviceable LaTeX
string wrapped in a bare ``<math display="block">...</math>`` tag. That is
the one piece of structure this book's born-digital PDF cannot give us any
other way: the text layer holds the rendered glyphs ("hi ←Mi1a1 + ...", with
subscripts fused onto their base letters and every symbol at body size), not
the source that produced them. Marker is therefore trusted here as the
*only* source for equation content, which is the opposite of the general
rule that text comes from the PDF layer -- there is no reliable PDF-layer
reading for a formula.

A numbered display equation prints its label at the right margin of the same
visual row, e.g. "hi ←Mi1a1 + · · · , (14.1)". Marker folds that label into
the same `<math>` block as literal trailing text: `..., \\quad (14.1)`. Row
merging in `pdfutil` then unifies it with the equation's own row on the PDF
side too, so a single bbox test suppresses both from the surrounding prose.
The label is pulled back out and re-attached as a KaTeX `\\tag{14.1}`, which
is what lets it render at the margin instead of inline, and as the block's
`label`/`anchor`, which is what lets "Equation (14.1)" resolve to a link.

One equation (14.10) arrives wrapped in a spurious `<table>` -- Marker's
layout heuristics occasionally mistake the two-column (equation, number)
layout for a table -- so the label is also searched for in the block's full
text, not just inside the `<math>` tag, to survive that shape without special
casing it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# Marker's fast-mode math OCR makes occasional character-level misreadings
# that produce syntactically valid, semantically wrong LaTeX -- exactly the
# class of "silent" defect that Gate 5 (KaTeX strict-mode rendering) and the
# numeric/coverage gates (which exclude equation interiors entirely, see
# stage2_reconcile) cannot catch, because nothing about the string is
# malformed or numerically different. Each entry below was found by visual
# adjudication (Gate 8) against the rendered chapter and then confirmed
# against this born-digital PDF's own embedded text glyphs -- not a second
# OCR guess -- via `page.get_text("words")` on the source page, which is
# ground truth for a digitally-typeset book. Matching is by exact page and
# exact original string, so if Marker's output ever changes upstream this
# silently becomes a no-op instead of corrupting a fixed equation.
_KNOWN_MARKER_TRANSCRIPTION_FIXES: dict[int, list[tuple[str, str]]] = {
    # p435 (chapter pg. 421): the hub-score variable "h" misread as "k" in
    # both angle-bracket terms of this unlabelled equation. Every other "k"
    # here (both exponents) is a genuine step-count index and is untouched.
    # PDF words at bbox y~434-451: "h⟨k⟩", "ck", "(MM T)kh⟨0⟩", "ck".
    435: [
        (
            r"\frac{k^{\langle k \rangle}}{c^k} = \frac{(MM^T)^k k^{\langle 0 \rangle}}{c^k}",
            r"\frac{h^{\langle k \rangle}}{c^k} = \frac{(MM^T)^k h^{\langle 0 \rangle}}{c^k}",
        )
    ],
    # p439 (chapter pg. 425), Equation (14.5): the "i" subscript that marks
    # "the share of node i's rank" was dropped from every N term. PDF words:
    # "N1ir1", "N2ir2", "Nnirn." -- i.e. N_{1i}, N_{2i}, N_{ni}, matching the
    # analogous, correctly-extracted hub/authority rule (14.2)'s M_{1i} etc.
    439: [
        (
            r"r_i \leftarrow N_1 r_1 + N_2 r_2 + \cdots + N_n r_n. \tag{14.5}",
            r"r_i \leftarrow N_{1i} r_1 + N_{2i} r_2 + \cdots + N_{ni} r_n. \tag{14.5}",
        )
    ],
    # p442 (chapter pg. 428), Equation (14.9): the same dropped-"i" defect,
    # plus the last term nested the subscript as N_{n_i} (N sub (n sub i))
    # instead of the two-character subscript N_{ni}. PDF words: "N1ib1",
    # "N2ib2", "Nnibn." -- i.e. N_{1i}, N_{2i}, N_{ni}.
    442: [
        (
            r"b_i \leftarrow N_1 b_1 + N_2 b_2 + \cdots + N_{n_i} b_n. \tag{14.9}",
            r"b_i \leftarrow N_{1i} b_1 + N_{2i} b_2 + \cdots + N_{ni} b_n. \tag{14.9}",
        )
    ],
}


def _apply_known_transcription_fixes(page: int, latex: str) -> str:
    for original, corrected in _KNOWN_MARKER_TRANSCRIPTION_FIXES.get(page, []):
        if latex == original:
            return corrected
    return latex


RE_TAG = re.compile(r"<[^>]+>")
RE_MATH = re.compile(r"<math[^>]*>(.*?)</math>", re.DOTALL)
# The label as Marker embeds it inside the math source: literal trailing
# "\quad (14.1)" after the expression, comma or period retained.
RE_TRAILING_QUAD_LABEL = re.compile(r"\s*\\quad\s*\(\s*(\d+)\.(\d+)\s*\)\s*$")
# The label wherever it ends up once every tag is stripped -- inside a
# spurious <table>'s second cell, or anywhere else Marker might place it.
RE_LABEL_AT_END = re.compile(r"\(\s*(\d+)\.(\d+)\s*\)\s*$")
# The label cell itself, when Marker wraps it in its own bare <math> tag
# instead of leaving it as plain text (Equation 22.12's shape, as opposed
# to 14.10's, where the same cell is plain text and RE_LABEL_AT_END finds
# it after tag-stripping instead).
RE_BARE_LABEL_MATH = re.compile(r"^\(\s*\d+\.\d+\s*\)$")
# What's left once every <math> tag in an Equation block is blanked out, for
# a genuine numbered equation: its own trailing punctuation (the sentence
# the formula ends does not stop being a sentence) and the margin label,
# nothing else. "Pr [defendant is guilty | ... only I-signal]" or "(i) a >
# c, or (ii) ..." leave far more residual text than this and are prose
# Marker mis-boxed as an equation, not a formula with a label.
RE_RESIDUAL_LABEL_ONLY = re.compile(r"^[.,;:]?\s*\(\s*\d+\.\d+\s*\)\s*$")


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", RE_TAG.sub(" ", s)).strip()


@dataclass
class Equation:
    """A recovered display equation, ready to emit as a KaTeX block."""

    page: int
    bbox: tuple[float, float, float, float]
    latex: str
    label: str | None = None
    anchor: str | None = None


def _parse_equation(node: dict) -> Equation | None:
    bbox = node.get("bbox")
    html_src = node.get("html") or ""
    if not bbox:
        return None

    math_matches = RE_MATH.findall(html_src)

    # The label cell is usually plain text once tag-stripped (14.10's shape,
    # handled below), but Marker sometimes wraps it in its own bare <math>
    # tag instead (22.12's shape). Either way it is not equation content,
    # and folding it into the joined math parts would print the label twice
    # -- once inline as literal text, once as the \tag this function adds.
    label_matches = [m for m in math_matches if RE_BARE_LABEL_MATH.match(html.unescape(m).strip())]
    content_matches = [m for m in math_matches if m not in label_matches]

    # Marker occasionally misreads a payoff matrix as an "Equation" shaped
    # like a table, with each single-letter strategy header wrapped in its
    # own spurious <math> tag ("<math>L</math> <math>R</math> ...") while the
    # actual payoffs sit in plain, unwrapped cells. A genuine numbered
    # equation that Marker tables (Equation 14.10) has exactly one <math>
    # tag and a bare label in the second cell. More than one <math> tag
    # inside a table is the signature of the former, not the latter, and the
    # matrix -- if it is payoff-shaped at all -- is recovered properly by
    # pipeline/matrix.py; folding its headers in here would both fabricate a
    # nonsense equation and, worse, exclude the matrix's own payoffs from
    # the coverage and numeric reference as if they were equation interior.
    if "<table" in html_src and len(content_matches) != 1:
        return None

    # Marker occasionally wraps a run of ordinary prose -- a sentence merely
    # *mentioning* a bare variable or two, a parenthesised list like "(i) a
    # > c, or (ii) ..." -- as a single "Equation" block, with the connective
    # English words left as plain text around one or more <math> tags.
    # Joining only the <math> contents (the path just below) silently drops
    # that connective text, which is real content the coverage gate expects
    # ("Pr [defendant is guilty | you have the only I-signal]" loses
    # everything but "I" this way). A genuine multi-line formula (e.g. two
    # stacked equations `a_1 = ...` / `b_2 = ...`) leaves nothing behind
    # here once its <math> tags are blanked out but whitespace, and a
    # genuine numbered equation leaves only its "(N.N)" label; both shapes
    # are left alone. Nothing else here carries a numbered label, so
    # nothing needs the anchor an Equation block provides -- returning None
    # falls through to the ordinary PDF-text-layer paragraph path, which
    # recovers every word verbatim (at the cost of the fused-subscript
    # styling a true formula gets from Marker's OCR).
    if content_matches:
        residual = _strip_tags(RE_MATH.sub(" ", html_src))
        if residual and not RE_RESIDUAL_LABEL_ONLY.match(residual):
            return None

    math_parts = [html.unescape(m).strip() for m in content_matches]
    latex = " ".join(p for p in math_parts if p).strip()
    if not latex:
        return _parse_textual_equation(html_src, bbox)
    return _finish_equation(html_src, latex, bbox)


# A display equation Marker's fast-mode OCR never wrapped in <math> at all --
# not a misreading, but a block with no math glyphs for the OCR to find, e.g.
# a ratio spelled out in English words with a horizontal rule standing in for
# the fraction bar. `_parse_equation` would otherwise return None for every
# one of these and the block would fall through to plain prose: harmless for
# text coverage (the words survive) but the numbered ones then have no
# anchor for "Equation (N.N)" to resolve to, and vanish from the structural
# equation count. Keyed by PDF page (1-indexed) and confirmed against the
# PDF's own text layer, in the same spirit as the transcription fixes above.
# The fallback built in `_parse_textual_equation` (every word in one
# `\text{}` run) stays in effect for any future occurrence not listed here;
# it is valid KaTeX and loses no content, just the source's visual layout.
_KNOWN_TEXTUAL_EQUATIONS: dict[int, str] = {
    # Equation (3.1): the neighborhood-overlap ratio, printed as two
    # centered lines over a rule rather than typeset math.
    71: (
        r"\frac{\text{number of nodes who are neighbors of both A and B}}"
        r"{\text{number of nodes who are neighbors of at least one of A or B}}"
    ),
    # Equation (4.1): the same ratio shape, applied to Wikipedia editors'
    # shared articles instead of shared neighbors.
    119: (
        r"\frac{\text{number of articles edited by both A and B}}"
        r"{\text{number of articles edited by at least one of A or B}}"
    ),
    # Equation (12.3): plain arithmetic with a subscript Marker rendered as
    # an HTML <sup> instead of OCR-ing as LaTeX.
    382: r"b_1 = py + (1 - p)b_2.",
}


def _parse_textual_equation(html_src: str, bbox: tuple) -> "Equation | None":
    plain = _strip_tags(html_src)
    match = RE_LABEL_AT_END.search(plain)
    if not match:
        return None
    label = f"{match.group(1)}.{match.group(2)}"
    body = RE_LABEL_AT_END.sub("", plain).strip().rstrip(",")
    latex = r"\text{" + body + "}" if body else label
    return Equation(
        page=0,  # filled in by the caller, which also applies the page-keyed fix
        bbox=tuple(bbox),
        latex=f"{latex} \\tag{{{label}}}",
        label=label,
        anchor=f"eq-{label.replace('.', '-')}",
    )


def _finish_equation(html_src: str, latex: str, bbox: tuple) -> "Equation | None":

    label: str | None = None
    match = RE_TRAILING_QUAD_LABEL.search(latex)
    if match:
        label = f"{match.group(1)}.{match.group(2)}"
        latex = RE_TRAILING_QUAD_LABEL.sub("", latex).rstrip()
    else:
        # Marker sometimes drops the label outside the <math> tag entirely
        # (the table-shaped block for Equation 14.10). The plain text of the
        # whole block still carries it as the last thing on the line.
        plain = _strip_tags(html_src)
        match = RE_LABEL_AT_END.search(plain)
        if match:
            label = f"{match.group(1)}.{match.group(2)}"

    anchor = f"eq-{label.replace('.', '-')}" if label else None
    if label:
        latex = f"{latex} \\tag{{{label}}}"

    return Equation(
        page=0,  # filled in by the caller, which tracks page from the tree walk
        bbox=tuple(bbox),
        latex=latex,
        label=label,
        anchor=anchor,
    )


def extract_equations(marker_payload: dict) -> list[Equation]:
    """Every display equation Marker found, across the whole chapter.

    Walks the raw Marker document tree rather than the flattened Region list
    that `stage2_reconcile.load_marker` builds, because that flattening runs
    the block's html through the same tag-stripping helper used for prose --
    which discards the LaTeX-bearing structure this needs and never unescapes
    the entities Marker leaves in place (`&gt;`, `&amp;`).
    """
    equations: list[Equation] = []

    def walk(node: dict, page: int | None) -> None:
        block_id = node.get("id") or ""
        parts = block_id.split("/")
        if len(parts) > 2 and parts[1] == "page":
            page = int(parts[2]) + 1  # Marker pages are 0-indexed
        if node.get("block_type") == "Equation":
            eq = _parse_equation(node)
            if eq is not None:
                eq.page = page or 0
                eq.latex = _apply_known_transcription_fixes(eq.page, eq.latex)
                if eq.label and eq.page in _KNOWN_TEXTUAL_EQUATIONS:
                    body = _KNOWN_TEXTUAL_EQUATIONS[eq.page]
                    eq.latex = f"{body} \\tag{{{eq.label}}}"
                equations.append(eq)
        for child in node.get("children") or []:
            walk(child, page)

    walk(marker_payload["document"], None)
    return equations
