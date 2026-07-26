"""Renders emitted Markdown back to plain text for comparison.

This is the candidate side of the coverage and numeric gates. It must undo
exactly the formatting the emitter added and nothing more -- anything dropped
here looks like a content loss that the pipeline did not actually cause.
"""

from __future__ import annotations

import re

_RE_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_RE_MDX_IMPORT = re.compile(r"^\s*(import|export)\s.+$", re.MULTILINE)
_RE_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Images contribute no text: their alt text is derived from the caption, which
# is emitted separately as prose. Counting both would double the caption.
_RE_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
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
_RE_EMPHASIS = re.compile(r"(\*\*|\*|__|_|`)")
_RE_TABLE_PIPE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.MULTILINE)
_RE_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_RE_LIST_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)


def markdown_to_text(md: str) -> str:
    """Plain text of an emitted chapter, with math excluded like an image."""
    text = _RE_FRONTMATTER.sub("", md)
    text = _RE_HTML_COMMENT.sub(" ", text)
    text = _RE_MDX_IMPORT.sub(" ", text)
    text = _RE_CODE_FENCE.sub(" ", text)
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
    text = _RE_EMPHASIS.sub("", text)
    return text
