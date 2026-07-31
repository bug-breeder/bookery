"""Renders emitted Markdown back to plain text for comparison.

This is the candidate side of the coverage and numeric gates. It must undo
exactly the formatting the emitter added and nothing more -- anything dropped
here looks like a content loss that the pipeline did not actually cause.
"""

from __future__ import annotations

import re

_RE_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
# The document's only level-1 heading is `display_title(meta)` verbatim --
# the same "N. Title" string already emitted as frontmatter `title`/
# `sidebar_label`. It exists for on-page display, not as body prose, so
# counting it here would double-count the title's words and (for chapter
# titles whose title text itself carries a digit, e.g. "Blockchain 101")
# introduce numerals the source page's own body text never repeats. Deeper
# headings (`##` and beyond) are real section titles copied from the PDF and
# are left in place; the negative lookahead on the hash run keeps this from
# also eating them.
_RE_H1 = re.compile(r"^[ \t]{0,3}#(?!#)[ \t]+.*\n?", re.MULTILINE)
# Matches only genuine JS/MDX import-export syntax (a quoted module
# specifier, or `export default`/`export const`/...), not prose that merely
# starts a sentence with the English word "import" or "export". Chapter 11's
# "import in Solidity allows the importing of symbols ..." is exactly such a
# sentence -- the bare `^(import|export)\s` version of this regex swallowed
# the whole paragraph because it never checked for real syntax after the
# keyword, which registered as a silent content loss against the reference.
_RE_MDX_IMPORT = re.compile(
    r"^[ \t]*import\s[^\n]*?\sfrom\s['\"][^'\"\n]+['\"];?[ \t]*$"
    r"|^[ \t]*export\s+(?:default\b|const\b|function\b|class\b|\{)[^\n]*$",
    re.MULTILINE,
)
_RE_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Images contribute no text: their alt text is derived from the caption, which
# is emitted separately as prose. Counting both would double the caption.
_RE_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# `stage4_emit`'s interior-text fallback prints a table/figure's own PDF text
# beneath its cropped image, for search and accessibility, whenever the block
# couldn't be parsed into structured cells. Gate 1 (recall) and Gate 2 (exact
# multiset equality) need opposite treatment of it: as extra candidate text
# it can only ever help Gate 1 -- a token the reference expects but the
# fallback's bbox-overlap trim missed still gets credited -- but it actively
# breaks Gate 2, since the same block's numbers are also excluded from the
# *reference* as figure/table-interior content (its OCR-recovered reading of
# an image is no more a character-for-character transcription than an
# equation's rendered glyphs are), so every number the fallback preserves
# would otherwise show up as "extra" against a reference that was always
# going to be missing it. Gate 2 therefore strips it; Gate 1 does not.
_RE_FIGURE_DATA = re.compile(r'<span class="figure-data">.*?</span>', re.DOTALL)
_RE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_RE_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
# Display and inline math contribute no text either, and for the same reason
# an image does not: a formula's rendered glyphs -- subscripts fused onto
# their base letters, symbols with no ToUnicode mapping -- are not a
# character-for-character reading of the LaTeX that reproduces them, so
# reducing the LaTeX to "operands" and diffing those against the PDF text
# layer compares two things that were never meant to match. Equations are
# verified independently: their count and labels against the structural
# gate, and their LaTeX against KaTeX strict mode in gate 5. The prose that
# cites them ("Equation (14.1)") sits outside these delimiters and is
# checked as ordinary text.
_RE_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
# A backslash is required inside the delimiters because the source prose
# itself contains plain dollar amounts, sometimes two or more in the same
# sentence ("...generate $80,000 of revenue... $40,000 of revenue...",
# Chapter 22's labor-market example). Without this guard the regex reads the
# first "$" as opening inline math and the *second dollar amount's* "$" as
# closing it, stripping the entire sentence between them -- including the
# first figure itself -- as if it were a formula. Genuine LaTeX emitted here
# always carries at least one command (\frac, \delta, ...); a bare currency
# figure never does.
_RE_INLINE_MATH = re.compile(r"\$([^$\n]*\\[^$\n]*)\$")
_RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
# Docusaurus explicit heading ids ("## 2.1 Basic Definitions {#sec-2-1}") are
# markup, not content. Left in place their digits register as numeric literals
# the source does not contain.
_RE_HEADING_ANCHOR = re.compile(r"[ \t]*\{#[A-Za-z0-9_.:-]+\}[ \t]*$", re.MULTILINE)
# The emitter's only inline emphasis is `**bold**` (figure/table caption
# labels, footnote markers); it never produces italics or inline code. A
# blanket "strip any * or _" rule -- the obvious first draft -- corrupts
# source prose that contains literal underscores or asterisks it didn't
# write: shell flags and identifiers from a code-heavy chapter
# ("rsa_keygen_bits", "-param_enc") silently fuse into one word once the
# underscore between them disappears, so "rsa" and "keygen" vanish from the
# token stream as if the pipeline had dropped them. Matching only genuine
# `**...**` pairs and keeping the wrapped text avoids that.
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_RE_TABLE_PIPE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.MULTILINE)
_RE_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_RE_LIST_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)


def markdown_to_text(md: str, strip_figure_data: bool = False) -> str:
    """Plain text of an emitted chapter, with math excluded like an image.

    ``strip_figure_data``, when set, also drops the interior-text fallback's
    own span (see the module-level note above `_RE_FIGURE_DATA`) -- pass
    this for Gate 2's candidate text, but not Gate 1's.
    """
    text = _RE_FRONTMATTER.sub("", md)
    text = _RE_H1.sub("", text)
    text = _RE_HTML_COMMENT.sub(" ", text)
    text = _RE_MDX_IMPORT.sub(" ", text)
    text = _RE_CODE_FENCE.sub(" ", text)
    if strip_figure_data:
        text = _RE_FIGURE_DATA.sub(" ", text)
    text = _RE_IMAGE.sub(" ", text)
    text = _RE_LINK.sub(r"\1", text)

    text = _RE_DISPLAY_MATH.sub(" ", text)
    text = _RE_INLINE_MATH.sub(" ", text)

    text = _RE_HTML_TAG.sub(" ", text)
    text = _RE_TABLE_PIPE.sub(" ", text)
    text = text.replace("|", " ")
    text = _RE_HEADING_ANCHOR.sub("", text)
    text = _RE_HEADING.sub("", text)
    text = _RE_BLOCKQUOTE.sub("", text)
    text = _RE_LIST_BULLET.sub("", text)
    text = _RE_BOLD.sub(r"\1", text)
    return text
