"""Read-side view of one chapter's canonical model, for lessons.

Everything a lesson is checked against comes through here: the reconciled
blocks (the only text a lesson may quote or take numbers from), the section
map (what a lesson must either cover or explicitly skip), and the exhibit
registry (the book's own figures and tables, by label or block id).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from pipeline import config, textnorm

# Block types whose text is the chapter's content. Captions and figure
# interior text are content too (a table's numbers live there), but they are
# collected separately so the pack can show them under their exhibit.
TEXT_TYPES = {"paragraph", "list", "exercise", "footnote", "code", "citation"}

RE_SHORT_ID = re.compile(r"^b\d{4}$")
RE_EXHIBIT_REF = re.compile(
    r"^(fig|figure|tbl|table)\s*([0-9]+(?:\.[0-9]+)*)$", re.IGNORECASE
)


@dataclass
class Section:
    label: str
    title: str
    block_id: str
    printed_page: int | None
    block_ids: list[str] = field(default_factory=list)
    words: int = 0


@dataclass
class Exhibit:
    ref: str              # "fig 2.4", "tbl 2.2", or the block id when unlabelled
    kind: str             # "figure" | "table" | "matrix"
    block_id: str
    label: str | None
    caption: str
    printed_page: int | None
    file: Path | None     # absolute path to the best raster on disk
    width: int | None
    height: int | None
    data: str             # text inside the exhibit (axis labels, table cells)


class ChapterSource:
    """One chapter's model, triage entry and assets, indexed for lookups."""

    def __init__(self, number: int, root: Path | None = None):
        self.number = number
        self.cid = config.chapter_id(number)
        self.root = root or config.ROOT
        work = self.root / "work"
        model_path = work / "reconcile" / f"{self.cid}.json"
        if not model_path.exists():
            raise FileNotFoundError(
                f"{model_path} does not exist -- run `bookery build` (PDF) or "
                "`bookery ingest --epub` (EPUB) for this chapter first."
            )
        self.model: dict = json.loads(model_path.read_text())
        triage_path = work / "triage.json"
        self.triage: dict = json.loads(triage_path.read_text()) if triage_path.exists() else {}
        assets_path = work / "assets.json"
        assets = json.loads(assets_path.read_text()) if assets_path.exists() else {}
        self.assets: list[dict] = assets.get(self.cid, [])
        self.blocks: list[dict] = self.model["blocks"]
        self.by_id: dict[str, dict] = {b["id"]: b for b in self.blocks}

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @cached_property
    def meta(self) -> dict:
        for ch in self.triage.get("boundary_map", {}).get("chapters", []):
            if ch["number"] == self.number:
                return ch
        return {"number": self.number, "title": self.model.get("title", "")}

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.model.get("title", f"Chapter {self.number}")

    @cached_property
    def book(self) -> dict:
        src = self.triage.get("source", {})
        info = self.triage.get("pdfinfo", {})
        return {
            "title": src.get("title") or info.get("Title") or self.root.name,
            "author": (src.get("author") or info.get("Author") or "").strip(" ;,"),
            "format": src.get("format", "pdf"),
        }

    @cached_property
    def page_offset(self) -> int | None:
        pdf, printed = self.meta.get("pdf_page"), self.meta.get("printed_page")
        if pdf is None or printed is None:
            return None
        return pdf - printed

    def printed_page(self, block: dict) -> int | None:
        page = block.get("page")
        if not page or self.page_offset is None:
            return None
        return page - self.page_offset

    # ------------------------------------------------------------------
    # Ids
    # ------------------------------------------------------------------

    def resolve_id(self, ref: str) -> str | None:
        """Accept a full block id ("ch02-b0017") or the pack's short form ("b0017")."""
        ref = ref.strip()
        if RE_SHORT_ID.match(ref):
            ref = f"{self.cid}-{ref}"
        return ref if ref in self.by_id else None

    @staticmethod
    def short_id(block_id: str) -> str:
        return block_id.rsplit("-", 1)[-1]

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    @cached_property
    def sections(self) -> list[Section]:
        """Top-level numbered sections ("2.1", not "2.1.3"), with their blocks.

        A lesson is held to covering-or-skipping each of these, never the
        subsections: condensing a chapter is precisely the decision of which
        subsections deserve a slide.
        """
        out: list[Section] = []
        current: Section | None = None
        for b in self.blocks:
            label = b.get("label")
            if b["type"] == "heading" and label and label.count(".") == 1:
                current = Section(
                    label=label,
                    title=re.sub(rf"^{re.escape(label)}\s*", "", b.get("text", "")).strip(),
                    block_id=b["id"],
                    printed_page=self.printed_page(b),
                )
                out.append(current)
            if current is not None:
                current.block_ids.append(b["id"])
                current.words += len(b.get("text", "").split())
        # EPUBs and unnumbered books carry no "N.M" labels at all.
        return out or self._unnumbered_sections()

    def _unnumbered_sections(self) -> list[Section]:
        """Sections for books whose headings carry no "N.M" label (most EPUBs)."""
        headings = [b for b in self.blocks if b["type"] == "heading"]
        levels = sorted({b.get("level") or 1 for b in headings})
        # The chapter title is usually the shallowest heading; sections are
        # the next level down. A chapter with a single heading level has no
        # sections to cover.
        if len(levels) < 2:
            return []
        level = levels[1]
        out: list[Section] = []
        current: Section | None = None
        for b in self.blocks:
            if b["type"] == "heading" and (b.get("level") or 1) == level:
                current = Section(
                    label=f"s{len(out) + 1}",
                    title=b.get("text", "").strip(),
                    block_id=b["id"],
                    printed_page=self.printed_page(b),
                )
                out.append(current)
            if current is not None:
                current.block_ids.append(b["id"])
                current.words += len(b.get("text", "").split())
        return out

    @cached_property
    def section_of(self) -> dict[str, str]:
        return {bid: s.label for s in self.sections for bid in s.block_ids}

    # ------------------------------------------------------------------
    # Exhibits
    # ------------------------------------------------------------------

    @cached_property
    def exhibits(self) -> list[Exhibit]:
        asset_by_block = {a["block"]: a for a in self.assets}
        out: list[Exhibit] = []
        seen_refs: set[str] = set()
        for b in self.blocks:
            if b["type"] not in ("figure", "matrix", "table"):
                continue
            label = b.get("label")
            kind_word = b.get("label_kind") or ("table" if b["type"] == "table" else "figure")
            ref = f"{'tbl' if kind_word == 'table' else 'fig'} {label}" if label else b["id"]
            # A table continued across pages repeats its label; the first
            # occurrence owns the label and continuations go by block id.
            if ref in seen_refs:
                ref = b["id"]
            seen_refs.add(ref)
            asset = asset_by_block.get(b["id"])
            file = None
            if asset:
                static = self.root / "site" / "static"
                for key in ("webp", "file"):
                    if asset.get(key) and (static / asset[key]).exists():
                        file = static / asset[key]
                        # Prefer the PNG for the pack (Claude can view it);
                        # build re-encodes whichever it gets.
                        if key == "file":
                            break
            data = " ".join(b.get("interior_text") or [])
            if b.get("cells"):
                data = " | ".join(" ; ".join(row) for row in b["cells"])
            out.append(
                Exhibit(
                    ref=ref,
                    kind=b["type"] if b["type"] != "figure" else kind_word,
                    block_id=b["id"],
                    label=label,
                    caption=b.get("caption") or "",
                    printed_page=self.printed_page(b),
                    file=file,
                    width=(asset or {}).get("width"),
                    height=(asset or {}).get("height"),
                    data=data,
                )
            )
        return out

    def resolve_exhibit(self, ref: str) -> Exhibit | None:
        ref = ref.strip()
        m = RE_EXHIBIT_REF.match(ref)
        if m:
            kind = "tbl" if m.group(1).lower().startswith("t") else "fig"
            ref = f"{kind} {m.group(2)}"
        else:
            ref = self.resolve_id(ref) or ref
        for ex in self.exhibits:
            if ex.ref == ref or ex.block_id == ref:
                return ex
        return None

    # ------------------------------------------------------------------
    # Text the lesson is checked against
    # ------------------------------------------------------------------

    def block_text(self, block: dict) -> str:
        """Every string a block contributes as source: text, caption, interior, cells, LaTeX."""
        parts = [block.get("text") or "", block.get("caption") or ""]
        parts += block.get("interior_text") or []
        for row in block.get("cells") or []:
            parts += row
        if block.get("latex"):
            parts.append(block["latex"])
        return " ".join(p for p in parts if p)

    @cached_property
    def full_text(self) -> str:
        return "\n".join(self.block_text(b) for b in self.blocks)

    @cached_property
    def normalized_blocks(self) -> dict[str, str]:
        return {b["id"]: textnorm.normalize(self.block_text(b)) for b in self.blocks}

    @cached_property
    def words(self) -> int:
        return sum(len((b.get("text") or "").split()) for b in self.blocks if b["type"] in TEXT_TYPES)


def lesson_dir(number: int, root: Path | None = None) -> Path:
    return (root or config.ROOT) / "lessons" / config.chapter_id(number)


def build_dir(number: int, root: Path | None = None) -> Path:
    return (root or config.ROOT) / "work" / "learn" / config.chapter_id(number)
