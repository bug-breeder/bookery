"""The canonical document model.

This is the single source of truth between extraction and emission. The MDX
is a projection of it, never the other way round. Markdown is deliberately
not used as the interchange format: it cannot express "this caption belongs
to figure 2.6", and that binding is exactly what must not be lost.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Block vocabulary. Anything an extractor produces is mapped onto one of
# these; an unmapped type is a bug rather than a passthrough.
BLOCK_TYPES = {
    "heading",
    "paragraph",
    "equation",
    "figure",
    "matrix",
    "table",
    "caption",
    "footnote",
    "citation",
    "exercise",
    "list",
    "code",
}

# A payoff matrix recovered from the text layer. It is a distinct type from
# both "figure" and "table" because it is neither: the book labels it
# "Figure N.N" and cross-references it as a figure, but it is emitted as a real
# Markdown table rather than a bitmap. Conflating it with "table" would make
# the structural gate expect a "Table N.N" caption that the book never wrote;
# conflating it with "figure" would make the asset gate demand a PNG that
# deliberately does not exist.
MATRIX = "matrix"

# Flags recorded on a block. Every one of these routes the block to the
# review queue; none of them are cosmetic.
FLAG_TYPE_DISAGREEMENT = "type_disagreement"
FLAG_CONTENT_DISAGREEMENT = "content_disagreement"
FLAG_NUMERIC_DISAGREEMENT = "numeric_disagreement"
FLAG_MISSING_IN_MARKER = "missing_in_marker"
FLAG_MISSING_IN_DOCLING = "missing_in_docling"
FLAG_EMPTY_TEXT = "empty_text"
FLAG_NEEDS_VISUAL = "needs_visual_adjudication"
FLAG_UNRESOLVED_CAPTION = "caption_without_figure"


@dataclass
class Block:
    id: str
    type: str
    page: int
    text: str = ""
    source: str = ""
    agreement: float = 1.0
    flags: list[str] = field(default_factory=list)

    # Structural extras, only set where meaningful.
    label: str | None = None          # "2.4" for Figure 2.4
    # Which keyword the PDF's own caption used -- "figure" or "table". Kept
    # apart from `type` because the two classify different things: `type`
    # says how the block is *rendered* (a bitmap crop, a recovered payoff
    # grid), while `label_kind` says what the book itself *called* it. A
    # book that numbers figures and tables in independent sequences (so
    # "Figure 2.1" and "Table 2.1" both exist) needs both to route each
    # exhibit to the right anchor prefix and cross-reference registry;
    # collapsing them the way earlier books could get away with -- every
    # captioned exhibit assumed to be a "Figure" -- silently overwrites one
    # exhibit's anchor and asset file with the other's.
    label_kind: str | None = None     # "figure" or "table"
    level: int | None = None          # heading depth
    anchor: str | None = None         # "fig-2-4"
    asset: str | None = None          # "img/ch02/fig-2-4.png"
    caption: str | None = None        # caption bound to a figure or table
    alt: str | None = None
    latex: str | None = None          # equation source
    cells: list[list[str]] | None = None  # table contents
    bbox: tuple[float, float, float, float] | None = None
    interior_text: list[str] | None = None  # text that belongs to a figure
    # Words the PDF sets in small caps. Their text layer is lowercase, so they
    # are re-presented in small caps rather than rewritten to uppercase, which
    # would change the characters the source actually contains.
    small_caps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in (None, [], "")}
        out.setdefault("flags", self.flags)
        out["agreement"] = round(self.agreement, 4)
        return out


@dataclass
class ChapterDoc:
    chapter: int
    title: str
    part: int | None
    pages: tuple[int, int]
    printed_pages: tuple[int, int]
    blocks: list[Block] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None

    def counts(self) -> dict:
        # A figure or matrix block counts as a "table" only if the PDF's own
        # caption called it one: some books number figures and tables in
        # independent sequences that collide ("Figure 2.1" and "Table 2.1"
        # both exist), so the caption keyword -- not the rendering `type` --
        # is what the structural gate must compare against the PDF's own
        # counts.
        return {
            "figures": [
                b.label
                for b in self.blocks
                if b.type in ("figure", MATRIX) and b.label and b.label_kind != "table"
            ],
            "tables": [
                b.label
                for b in self.blocks
                if b.type in ("figure", MATRIX, "table")
                and b.label
                and b.label_kind == "table"
            ],
            "sections": [b.label for b in self.blocks if b.type == "heading" and b.label],
            "equations": [b.label for b in self.blocks if b.type == "equation" and b.label],
            "exercises": sum(1 for b in self.blocks if b.type == "exercise"),
            "footnotes": sum(1 for b in self.blocks if b.type == "footnote"),
            # Reported separately so a chapter's matrices are visible in the
            # artifact rather than hidden inside the figure count.
            "matrices": sum(1 for b in self.blocks if b.type == MATRIX),
            "labelled_as_table": [
                b.label for b in self.blocks if b.type == MATRIX and b.label
            ],
        }

    def as_dict(self) -> dict:
        return {
            "chapter": self.chapter,
            "title": self.title,
            "part": self.part,
            "pages": list(self.pages),
            "printed_pages": list(self.printed_pages),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "counts": self.counts(),
            "anchors": self.anchors,
            "references": self.references,
            "dropped": self.dropped,
            "blocks": [b.as_dict() for b in self.blocks],
        }
