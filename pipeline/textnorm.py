"""Text normalisation shared by reconciliation and the verification harness.

The gates compare extracted text against a reference, so both sides must be
normalised identically. Keeping that logic in one module is what makes the
comparison meaningful rather than an accident of which regexes ran where.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# --------------------------------------------------------------------------
# Accent repair.
#
# The book's Computer Modern fonts carry no ToUnicode CMap (1% coverage), so
# accented letters survive extraction only as an accent glyph plus a base
# letter -- and the two extractors break them in *different* ways:
#
#   pdftotext : base letter followed by a combining mark   'Erdo' U+0308 's'
#   PyMuPDF   : spacing accent followed by the base letter 'Erd' U+00A8 'os'
#
# Both are repaired to the same precomposed form so the gates can compare
# them. The rule is purely mechanical -- it recombines marks that the font
# already placed together and never substitutes one letter for another.
#
# Note on Erdös: this book prints a diaeresis, not the Hungarian double
# acute of "Erdős". Verified by rendering p.51 at 400dpi. Rewriting it to
# "Erdős" would be correcting the authors, which the brief forbids.
# --------------------------------------------------------------------------

# Spacing accent glyph -> combining codepoint.
_ACCENTS = {
    "\u00a8": "\u0308",  # diaeresis
    "\u00b4": "\u0301",  # acute
    "\u02dd": "\u030b",  # double acute
    "\u02c6": "\u0302",  # circumflex
    "\u02c7": "\u030c",  # caron
    "\u02dc": "\u0303",  # tilde
    "\u00b8": "\u0327",  # cedilla
    "\u02da": "\u030a",  # ring above
}

# Glyphs that land in the Unicode private-use area are unmapped font glyphs,
# not characters. They are logged rather than guessed at; the only known
# occurrence sits inside Figure 16.1's Venn diagram.
PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")

_RE_SPACING_ACCENT = re.compile(
    "([" + "".join(re.escape(a) for a in _ACCENTS) + "])([a-zA-Z])"
)


def repair_accents(text: str) -> str:
    """Recombine split accent glyphs into precomposed characters.

    Handles the accent-before-letter form; the letter-before-combining-mark
    form is handled by the NFC pass that follows.
    """

    def _combine(match: re.Match[str]) -> str:
        accent, letter = match.group(1), match.group(2)
        return unicodedata.normalize("NFC", letter + _ACCENTS[accent])

    text = _RE_SPACING_ACCENT.sub(_combine, text)
    return unicodedata.normalize("NFC", text)


# --------------------------------------------------------------------------
# Normalisation used by the coverage gate.
# --------------------------------------------------------------------------

_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
}

_QUOTES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2032": "'",
}

_DASHES = {
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
}

_RE_HYPHEN_BREAK = re.compile(r"(\w)[\u00ad\-]\s*\n\s*(\w)")
_RE_WS = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Canonical form for text comparison.

    Discards exactly the things the brief puts out of scope: line breaks,
    hyphenation at line ends, ligature encoding, and quote/dash styling.
    """
    s = repair_accents(s)
    s = _RE_HYPHEN_BREAK.sub(r"\1\2", s)
    for table in (_LIGATURES, _QUOTES, _DASHES):
        for src, dst in table.items():
            s = s.replace(src, dst)
    s = unicodedata.normalize("NFKC", s)
    s = _RE_WS.sub(" ", s)
    return s.strip().lower()


_RE_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def tokens(s: str) -> list[str]:
    """Word tokens used for the recall gate."""
    return _RE_TOKEN.findall(normalize(s))


# --------------------------------------------------------------------------
# Citation stream.
#
# Bracketed citations must be pulled out before the numeric gate runs,
# otherwise reference numbers are compared as if they were content numbers.
# --------------------------------------------------------------------------

_RE_CITATION = re.compile(r"\[\s*\d{1,3}(?:\s*,\s*\d{1,3})*\s*\]")


def extract_citations(s: str) -> tuple[str, list[int]]:
    """Strip bracketed citations, returning the remaining text and the refs."""
    refs: list[int] = []

    def _take(match: re.Match[str]) -> str:
        refs.extend(int(n) for n in re.findall(r"\d+", match.group(0)))
        return " "

    return _RE_CITATION.sub(_take, s), refs


# A numeric literal: integers and decimals, but not the digits embedded inside
# identifiers. The bare-decimal alternative is not cosmetic: this book writes
# probabilities and payoffs without a leading zero -- ".48, .12" in the
# Marketing Strategy game, "q = .42" in the penalty-kick analysis -- and
# requiring a leading digit made every one of them invisible to the numeric
# gate. Because it was invisible on both sides of the comparison the gate still
# passed, which is the worst way for a check to be wrong.
# The optional ordinal suffix keeps "the 500th and 501st nodes" countable.
# Digits followed by letters are otherwise excluded, to avoid reading the
# digits inside identifiers as numbers, and an ordinal is the one case where
# those letters belong to the word but the number is still a number.
_RE_NUMBER = re.compile(
    r"(?<![\w.])(?:\d+(?:\.\d+)?|\.\d+)(?:st|nd|rd|th)?(?![\w])", re.IGNORECASE
)
_RE_ORDINAL_SUFFIX = re.compile(r"(?:st|nd|rd|th)$", re.IGNORECASE)


def numbers(s: str, drop_citations: bool = True) -> list[str]:
    """Numeric literals used by the numeric-fidelity gate."""
    text = normalize(s)
    if drop_citations:
        text, _ = extract_citations(text)
    out = []
    for raw in _RE_NUMBER.findall(text):
        raw = _RE_ORDINAL_SUFFIX.sub("", raw)
        # A missing leading zero is typography, not information, so ".48" and
        # "0.48" are the same literal.
        if raw.startswith("."):
            raw = "0" + raw
        # Normalise trailing-zero decimals so 2.0 and 2.00 compare equal,
        # while keeping 2.0 distinct from 2 (a real difference in a table).
        out.append(raw.rstrip("0").rstrip(".") if "." in raw else raw)
    return out


# --------------------------------------------------------------------------
# Figure integer-soup detection.
#
# Vector diagrams (the karate-club graphs, axis tick labels) extract as long
# runs of bare integers with no sentence structure. Left in the prose they
# corrupt both the coverage gate and the numeric gate, so they are detected
# here and routed to the owning figure.
# --------------------------------------------------------------------------

_RE_BARE_INT_RUN = re.compile(r"(?:(?<=\s)|^)(\d+(?:\.\d+)?)(?=\s|$)")


_RE_BARE_NUMBER = re.compile(r"[-\u2212]?\d+(?:\.\d+)?")


def integer_soup_ratio(s: str) -> float:
    """Fraction of whitespace-separated fields that are bare numbers.

    TeX sets a negative number's sign as U+2212 (minus sign), not the
    ASCII hyphen, so a payoff table of negative integers ("\u221265 \u221251
    \u221237 ...") would otherwise score as prose -- none of its fields
    would match a digits-only pattern -- and slip past every caller of this
    function that exists to keep such tables out of the reference text.
    """
    fields = s.split()
    if not fields:
        return 0.0
    bare = sum(1 for f in fields if _RE_BARE_NUMBER.fullmatch(f))
    return bare / len(fields)


def for_output(text: str) -> str:
    """Text as it should appear in the emitted document.

    Expands the typographic ligatures TeX uses for rendering. They are a
    property of the typeface, not of the word: leaving U+FB01 in the output
    would break both search and copy-paste for every "fi" in the book.
    """
    text = repair_accents(text)
    for ligature, expansion in _LIGATURES.items():
        text = text.replace(ligature, expansion)
    return text


_RE_MIDLINE_HYPHEN = re.compile(r"\b([A-Za-z]{2,})-([A-Za-z]{2,})\b")


def collect_hyphenated_forms(text: str) -> set[str]:
    """Hyphenated words that occur intact mid-line.

    Used to decide whether a hyphen at a line break is real ("well-known")
    or discretionary TeX hyphenation ("net-work"). Driving that from the
    document's own vocabulary beats guessing.
    """
    return {
        f"{a.lower()}-{b.lower()}" for a, b in _RE_MIDLINE_HYPHEN.findall(text)
    }


def join_hyphenated(left: str, right: str, known_hyphenated: set[str]) -> str:
    """Join a line ending in a hyphen with the line that follows it."""
    # A year range like "1872-1907" is sometimes set with an en dash and wraps
    # at the dash under column layout, e.g. "1872\u2013" / "81". That is not a
    # hyphenated word split, so it does not belong to `known_hyphenated`, but
    # it is still one token: the digits either side of the dash are joined
    # with no space, unlike an ordinary hyphen-vs-en-dash-terminated line.
    if left.endswith("\u2013") and left[-2:-1].isdigit() and right[:1].isdigit():
        return left + right
    if not left.endswith("-"):
        return left + " " + right
    stem = left[:-1]
    head = re.split(r"\s", stem)[-1]
    tail = re.split(r"\s", right)[0].strip(".,;:)")
    if f"{head.lower()}-{tail.lower()}" in known_hyphenated:
        return left + right
    return stem + right


def is_integer_soup(s: str, min_run: int = 6, ratio: float = 0.8) -> bool:
    """True when a text run looks like figure content rather than prose.

    Requires both a minimum length and a high bare-number ratio so that a
    short numeric phrase inside real prose is never misclassified.
    """
    fields = s.split()
    if len(fields) < min_run:
        return False
    if integer_soup_ratio(s) < ratio:
        return False
    # Genuine prose almost always carries sentence punctuation.
    if re.search(r"[.!?;:]\s", s):
        return False
    # Axis ticks are drawn from a range and so are close to all-distinct; a
    # run dominated by one repeated value is something else wearing the same
    # shape -- e.g. a domain-parameter listing such as "a = 00000000 00000000
    # ... 00000000", where a curve coefficient of zero prints as eight
    # identical all-digit hex words. That is prose describing a labelled
    # value, not a chart's tick marks, and belongs in the reference text.
    bare = [f for f in fields if _RE_BARE_NUMBER.fullmatch(f)]
    if bare and Counter(bare).most_common(1)[0][1] / len(bare) > 0.5:
        return False
    return True


def is_letter_soup(s: str, min_letters: int = 10, max_distinct: int = 3) -> bool:
    """True when a text run is a repeated-letter sequence rather than prose.

    Behavioral Game Theory's Appendix 5 prints raw experimental choices as
    strings like "abbbbbb" and "aAAbbbb" (a run's letter records one of two
    actions per period): a table of these, set at footnote size at the foot
    of the page, otherwise reads exactly like a footnote's opening line --
    small font, bottom of the page, starts with a bare number ("12
    aaabbbb aaabbb"). Genuine footnote prose always draws on a normal
    alphabet's worth of distinct letters; a run built from two or three
    repeated symbols is a sequence being tabulated, not a sentence.
    """
    letters = re.sub(r"[^a-zA-Z]", "", s)
    if len(letters) < min_letters:
        return False
    return len(set(letters.lower())) <= max_distinct
