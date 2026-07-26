"""Payoff matrix recovery from the PDF text layer.

Chapter 6 contains 28 captioned exhibits and 24 of them are payoff matrices:
grids of numbers with strategy names on both axes. Neither Marker nor Docling
reports them as tables or as pictures, because TeX draws them with `\\put`
rules rather than as a tabular environment -- there is no table structure in
the file to find. Both extractors therefore emit the cells as loose text, the
caption is left unbound, and the numbers land in the reference as unattributed
digits. That is how 24 of this chapter's exhibits went missing.

They are recovered here from geometry instead, which is the only source that
actually describes them:

    Your Partner                <- column player, roman, above the headers
       Presentation   Exam      <- column strategies, text italic
  You  Presentation  90, 90  86, 92
       Exam          92, 86  88, 88
                                <- "Figure 6.1: ..." caption below

A payoff cell is a comma-separated tuple of numbers set in the math italic
face; two or more such cells sharing a baseline make a row, and two or more
rows make a grid. Strategy names are the italic runs to the left of the cells
and above them; the player names are the roman runs outside those.

The book labels these exhibits "Figure", not "Table", and cross-references
call them Figure 6.1. They keep the figure label and the `fig-` anchor and are
counted as figures; only their *presentation* differs, as a real Markdown
table rather than a bitmap, which keeps the payoffs selectable, searchable and
legible to a screen reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdfutil import Line

# A payoff: two or three signed numbers separated by commas. Every part of
# this pattern is here because a real matrix in the book needs it: TeX sets
# the minus as U+2212; Matching Pennies writes its payoffs as "+1, -1" with an
# explicit plus; the Marketing Strategy game uses bare decimals with no
# leading zero (".48, .12"); three-player games in the exercises carry
# triples. Requiring a leading digit lost two whole exhibits.
NUMBER = r"[-+\u2212\u2013]?(?:\d+(?:\.\d+)?|\.\d+)(?:/\d+)?"
# A payoff can also be a single symbolic variable rather than a number: the
# General Symmetric Game (Figure 7.3) states the abstract 2x2 game as
# "a, a / b, c / c, b / d, d" to derive the evolutionary-stability condition
# for arbitrary payoffs, rather than illustrating it with one concrete game.
# Restricted to a lone letter, optionally signed, primed, or subscripted, so
# this cannot match ordinary math variables floating in prose -- those are
# never alone on a payoff-shaped "x, y" line in math italic outside a grid.
# The subscript can be a bare trailing letter with no underscore glyph at
# all: Chapter 19's node-labelled payoffs ("av, aw", read a_v and a_w) set
# the subscript in a smaller math font (CMMI8) with no separator character
# in the text layer, and its "Coordination Game with a bilingual option"
# also signs a symbolic payoff outright ("-y, -y").
# Restricted to lowercase: every symbolic payoff in the book is a math-
# italic lowercase variable. Strategy-name abbreviations are two-letter
# uppercase combinations ("AA", "AB", the normal-form conversion of Figure
# 6.25's dynamic game into simultaneous strategies) and, uppercase allowed,
# match this shape too -- "AA, AB" reads as two signed one-letter symbols
# exactly like a genuine payoff pair, pulling a chapter 6 matrix's own
# column-header row into its grid as a spurious extra row of "payoffs".
SYMBOL = r"[-+\u2212\u2013]?[a-z](?:['\u2032]|_?[a-zA-Z\d])?"
# A payoff cell can also be a parenthesised "larger of" expression: the
# Coordination Game with a Bilingual Option (Figure 19.18) writes its AB-AB
# payoff as "(a, b)+, (a, b)+", denoting max(a, b) for each player. Treated
# as one atomic cell component so its internal comma does not get read as
# the boundary between two cells.
COMPOUND = r"\(\s*[a-zA-Z]\s*,\s*[a-zA-Z]\s*\)\+"
CELL = rf"(?:{NUMBER}|{SYMBOL}|{COMPOUND})"
RE_PAYOFF = re.compile(rf"^{CELL}(?:\s*,\s*{CELL}){{1,2}}$")

# Computer Modern's math italic carries the payoff digits; text italic carries
# the strategy names. Distinguishing them is what separates a cell from a
# header without resorting to a word list.
MATH_FONT_HINTS = ("CMMI", "CMSY", "CMEX")
ITALIC_FONT_HINTS = ("CMTI", "CMMI", "Italic", "Oblique")

ROW_TOLERANCE = 4.0       # baseline jitter within one matrix row
COLUMN_TOLERANCE = 26.0   # cell centres belonging to the same column
HEADER_GAP = 34.0         # how far above the grid a header row may sit
LABEL_GAP = 30.0          # how far above the headers the player name may sit
CAPTION_GAP = 40.0        # how far below a degenerate grid its caption may sit


# A component that is nothing but zeros ("000") is never a genuine payoff --
# a real zero payoff is always written bare, as "0" -- but is exactly the
# shape TeX's thousands-grouped denominators leave behind in the text layer
# once the grouping comma survives extraction: Chapter 21's "200,000" (an
# equation's plain-English denominator, not a game) reads character-for-
# character like the payoff pair "200, 000" and would otherwise be pulled
# into the payoff-cell reference as a fabricated matrix entry.
RE_ZERO_PAD = re.compile(r"^[-+\u2212\u2013]?0{2,}$")


def is_payoff(text: str) -> bool:
    text = text.strip()
    if not RE_PAYOFF.match(text):
        return False
    return not any(RE_ZERO_PAD.match(part.strip()) for part in text.split(","))


def _is_math(line: Line) -> bool:
    return any(hint in font for font in line.fonts for hint in MATH_FONT_HINTS)


def _is_italic(line: Line) -> bool:
    return any(hint in font for font in line.fonts for hint in ITALIC_FONT_HINTS)


HEADER_TEXT_MAX_CHARS = 20  # a strategy name, not a wrapped prose line


def _is_header_like(line: Line) -> bool:
    """Strategy names are usually italic, but not always.

    Figure 7.7's virus strategies ("Φ6", "ΦH2") are set in plain roman type,
    the same face as the surrounding prose, so requiring italic missed the
    whole matrix. A strategy name is still reliably short -- a word or two --
    where a paragraph line reaching this far into the header search radius is
    a full sentence. Combined with the tight vertical and column-alignment
    bounds every caller already applies, text length is what tells the two
    apart here, not the font.
    """
    return _is_italic(line) or len(line.text.strip()) <= HEADER_TEXT_MAX_CHARS


def _centre(line: Line) -> float:
    return (line.bbox[0] + line.bbox[2]) / 2.0


@dataclass
class Matrix:
    """A recovered payoff matrix, ready to emit as a Markdown table."""

    page: int
    cells: list[list[str]]
    bbox: tuple[float, float, float, float]
    row_player: str | None = None
    col_player: str | None = None
    lines: list[Line] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return len(self.cells) - 1

    @property
    def columns(self) -> int:
        return len(self.cells[0]) - 1 if self.cells else 0

    def corner(self) -> str:
        """Top-left cell naming both players, e.g. 'You \\ Your Partner'."""
        if self.row_player and self.col_player:
            return f"{self.row_player} \\ {self.col_player}"
        return self.row_player or self.col_player or ""

    def as_table(self) -> list[list[str]]:
        table = [list(row) for row in self.cells]
        table[0][0] = self.corner()
        return table


def _cluster_rows(cells: list[Line]) -> list[list[Line]]:
    rows: list[list[Line]] = []
    for cell in sorted(cells, key=lambda l: (l.bbox[1], l.bbox[0])):
        for row in rows:
            if abs(row[0].bbox[1] - cell.bbox[1]) <= ROW_TOLERANCE:
                row.append(cell)
                break
        else:
            rows.append([cell])
    for row in rows:
        row.sort(key=lambda l: l.bbox[0])
    return rows


def _column_centres(rows: list[list[Line]]) -> list[float]:
    centres: list[float] = []
    for line in sorted((l for row in rows for l in row), key=lambda l: l.bbox[0]):
        centre = _centre(line)
        for index, existing in enumerate(centres):
            if abs(existing - centre) <= COLUMN_TOLERANCE:
                centres[index] = (existing + centre) / 2.0
                break
        else:
            centres.append(centre)
    return sorted(centres)


def _nearest_column(centre: float, centres: list[float]) -> int | None:
    best, best_distance = None, COLUMN_TOLERANCE
    for index, column in enumerate(centres):
        distance = abs(column - centre)
        if distance < best_distance:
            best, best_distance = index, distance
    return best


def find_matrices(
    lines: list[Line], page: int, caption_tops: tuple[float, ...] = ()
) -> list[Matrix]:
    """Recover every payoff matrix on a page from unmerged text lines.

    `lines` must be unmerged: `merge_rows` concatenates a matrix row into one
    string, which destroys the column positions this depends on.

    `caption_tops` are the y positions of "Figure N.N:" captions on the page.
    They are what makes a degenerate one-cell grid safe to accept: iterated
    deletion of dominated strategies leaves games with a single surviving
    outcome, and Figure 6.22 is exactly that, so requiring two rows would drop
    it. A lone payoff is only believed when a caption sits directly beneath it.
    """
    candidates = [l for l in lines if is_payoff(l.text) and _is_math(l)]
    if not candidates:
        return []

    matrices: list[Matrix] = []
    for group, degenerate in _group_grids(candidates, caption_tops):
        matrix = _build(group, lines, page, strict=degenerate)
        if matrix is not None:
            matrices.append(matrix)
    return matrices


def _group_grids(
    cells: list[Line], caption_tops: tuple[float, ...]
) -> list[tuple[list[Line], bool]]:
    """Split a page's payoff cells into separate grids.

    A page can carry several matrices -- a game and its variant, or three
    exercises -- so cells are grouped by vertical proximity before a grid is
    assembled: a gap larger than a few lines starts a new exhibit. Returns
    each group with a flag marking whether it needs the stricter checks that
    a degenerate grid is held to.
    """
    rows = _cluster_rows(cells)
    groups: list[list[list[Line]]] = []
    for row in rows:
        if groups:
            previous = groups[-1][-1]
            gap = row[0].bbox[1] - previous[0].bbox[3]
            if gap <= 3.0 * (previous[0].bbox[3] - previous[0].bbox[1]):
                groups[-1].append(row)
                continue
        groups.append([row])

    out: list[tuple[list[Line], bool]] = []
    for group in groups:
        flat = [l for row in group for l in row]
        if len(group) >= 2 and len(flat) >= 2:
            out.append((flat, False))
            continue
        bottom = max(l.bbox[3] for l in flat)
        if any(0 <= top - bottom <= CAPTION_GAP for top in caption_tops):
            out.append((flat, True))
    return out


def _build(
    cells: list[Line], lines: list[Line], page: int, strict: bool = False
) -> Matrix | None:
    rows = _cluster_rows(cells)
    centres = _column_centres(rows)
    if not rows or not centres:
        return None
    if not strict and (len(rows) < 2 or len(centres) < 2):
        return None

    grid: list[list[str]] = [["" for _ in centres] for _ in rows]
    for r, row in enumerate(rows):
        for cell in row:
            c = _nearest_column(_centre(cell), centres)
            if c is None:
                return None
            grid[r][c] = _clean(cell.text)

    left = min(l.bbox[0] for l in cells)
    right = max(l.bbox[2] for l in cells)
    top = min(l.bbox[1] for l in cells)
    bottom = max(l.bbox[3] for l in cells)
    consumed = list(cells)

    # Row strategy names: the italic run left of each row's first cell.
    row_headers: list[str] = []
    for row in rows:
        header = _pick(
            lines,
            lambda l, row=row: (
                _is_header_like(l)
                and not is_payoff(l.text)
                and abs(l.bbox[1] - row[0].bbox[1]) <= ROW_TOLERANCE
                and l.bbox[2] <= row[0].bbox[0] + 2.0
            ),
            key=lambda l: -l.bbox[2],
        )
        row_headers.append(_clean(header.text) if header else "")
        if header:
            consumed.append(header)
            left = min(left, header.bbox[0])

    # Column strategy names: italic runs above the grid, one per column.
    col_headers: list[str] = ["" for _ in centres]
    header_lines = sorted(
        (
            l
            for l in lines
            if not is_payoff(l.text)
            and _is_header_like(l)
            and 0 <= top - l.bbox[3] <= HEADER_GAP
        ),
        # Nearest to the grid first: a column player's name (e.g. "Organism
        # 2", sitting a full row above the strategy names) can fall inside
        # this same gap once roman-font text is allowed as a header
        # candidate, and would otherwise steal a column from the strategy
        # name that is actually adjacent to the grid, purely because of
        # whichever order `lines` happened to list them in.
        key=lambda l: top - l.bbox[3],
    )
    header_top = top
    for line in header_lines:
        c = _nearest_column(_centre(line), centres)
        if c is not None and not col_headers[c]:
            col_headers[c] = _clean(line.text)
            consumed.append(line)
            header_top = min(header_top, line.bbox[1])

    # A grid too small to speak for itself must name both of its axes, or it
    # is an inline number pair rather than a game.
    if strict:
        if not all(col_headers) or not all(row_headers):
            return None
    elif not any(col_headers) and not any(row_headers):
        return None

    # Player names: usually roman runs outside the strategy names, but a
    # game whose players are graph nodes (Chapter 19's "v" and "w") names
    # them with single-letter math-italic variables instead -- the same
    # CMMI face the strategy names themselves use elsewhere in the book.
    # Font alone can't tell those two apart, so a short run is accepted
    # regardless of style; position (strictly above the header row, or
    # strictly left of the grid) is what already keeps this from grabbing
    # an actual strategy name, which never sits in either zone.
    col_player = _pick(
        lines,
        lambda l: (
            (not _is_italic(l) or len(_clean(l.text)) <= 3)
            and not is_payoff(l.text)
            and 0 < header_top - l.bbox[3] <= LABEL_GAP
            and left - 40 <= _centre(l) <= right + 40
        ),
        key=lambda l: -l.bbox[3],
    )
    row_player = _pick(
        lines,
        lambda l: (
            (not _is_italic(l) or len(_clean(l.text)) <= 3)
            and not is_payoff(l.text)
            and l.bbox[2] <= left + 2.0
            and top - ROW_TOLERANCE <= l.bbox[3] <= bottom + ROW_TOLERANCE
        ),
        key=lambda l: -l.bbox[2],
    )
    for line in (col_player, row_player):
        if line:
            consumed.append(line)

    table = [[""] + col_headers]
    for header, row in zip(row_headers, grid):
        table.append([header] + row)

    box = (
        min(l.bbox[0] for l in consumed),
        min(l.bbox[1] for l in consumed),
        max(l.bbox[2] for l in consumed),
        max(l.bbox[3] for l in consumed),
    )
    return Matrix(
        page=page,
        cells=table,
        bbox=box,
        row_player=_clean(row_player.text) if row_player else None,
        col_player=_clean(col_player.text) if col_player else None,
        lines=consumed,
    )


def _pick(lines: list[Line], predicate, key) -> Line | None:
    matches = [l for l in lines if predicate(l)]
    return min(matches, key=key) if matches else None


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # A literal pipe would end the Markdown table cell early.
    return text.replace("|", "\\|")
